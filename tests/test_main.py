"""``main._build_modules`` 的装配逻辑本身——同 ``be-shell-go``
``cmd/shell/main_test.go`` 的判断，用手写夹具覆盖"按外壳挑模块"/"外壳
不存在时报错"/"按 BRICKKIT_SERVED_MEMBERS 筛成员的四种状态"，不需要
真实基础设施（不连 DB/NATS，``_build_modules`` 本身也不连）。

阶段四附加 Task 0.2/0.3：SHELL_ENV_JSON（产出 7）整体退休，
BRICKKIT_SERVED_MEMBERS 取代它成为"这次谁真的被收编"的数据源，模块
自己的 configSchema 值改由 shell-config.json 的 config 字段直接携带。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import main


def _write_json(path: Path, data: object) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_已知的唯一真实组件都在MODULE_REGISTRY里() -> None:
    main._register_known_modules()
    assert set(main._MODULE_REGISTRY) == {"infra/print"}
    assert callable(main._MODULE_REGISTRY["infra/print"])


def test_按SHELL_NAME挑出对应外壳_按BRICKKIT_SERVED_MEMBERS再筛一遍(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_json(
        tmp_path / "shell-config.json",
        [
            {
                "name": "py-render",
                "modules": [
                    {
                        "componentId": "infra/print",
                        "version": "1.0.5",
                        "schema": "infra_print",
                        "httpPort": 8400,
                        "extraPorts": {"grpc": 9400},
                        "config": {"COMPONENT_ID": "infra/print"},
                    }
                ],
            }
        ],
    )
    monkeypatch.setenv("SHELL_CONFIG_JSON", config_path)
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS", "infra-print-1-0-5")

    specs = main._build_modules("py-render")

    assert len(specs) == 1
    spec = specs[0]
    assert spec.component_id == "infra/print"
    assert spec.component_version == "1.0.5"
    assert spec.http_port == 8400
    assert spec.extra_ports == {"grpc": 9400}
    assert spec.schema == "infra_print"
    assert spec.env == {"COMPONENT_ID": "infra/print"}
    assert callable(spec.new_module)


def test_BRICKKIT_SERVED_MEMBERS未列出的成员被跳过不报错(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write_json(
        tmp_path / "shell-config.json",
        [
            {
                "name": "py-render",
                "modules": [
                    {"componentId": "infra/print", "version": "1.0.5", "schema": "infra_print", "httpPort": 8400}
                ],
            }
        ],
    )
    monkeypatch.setenv("SHELL_CONFIG_JSON", config_path)
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS", "")

    specs = main._build_modules("py-render")

    assert specs == []


def test_BRICKKIT_SERVED_MEMBERS里有shell_config找不到的成员时报错(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _write_json(
        tmp_path / "shell-config.json",
        [
            {
                "name": "py-render",
                "modules": [
                    {"componentId": "infra/print", "version": "1.0.5", "schema": "infra_print", "httpPort": 8400}
                ],
            }
        ],
    )
    monkeypatch.setenv("SHELL_CONFIG_JSON", config_path)
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS", "infra-print-1-0-5,mdm-customer-1-0-7")

    with pytest.raises(RuntimeError, match="BRICKKIT_SERVED_MEMBERS"):
        main._build_modules("py-render")


def test_BRICKKIT_SERVED_MEMBERS未设置时报错(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write_json(tmp_path / "shell-config.json", [{"name": "py-render", "modules": []}])
    monkeypatch.setenv("SHELL_CONFIG_JSON", config_path)
    monkeypatch.delenv("BRICKKIT_SERVED_MEMBERS", raising=False)

    with pytest.raises(RuntimeError, match="BRICKKIT_SERVED_MEMBERS"):
        main._build_modules("py-render")


def test_外壳在shell_config里不存在时报错(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write_json(tmp_path / "shell-config.json", [{"name": "py-render", "modules": []}])
    monkeypatch.setenv("SHELL_CONFIG_JSON", config_path)
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS", "")

    with pytest.raises(RuntimeError, match="shell-config.json"):
        main._build_modules("go-infra")


def test_SHELL_CONFIG_JSON未设置时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHELL_CONFIG_JSON", raising=False)
    monkeypatch.delenv("BRICKKIT_SERVED_MEMBERS", raising=False)
    with pytest.raises(RuntimeError, match="SHELL_CONFIG_JSON"):
        main._build_modules("py-render")
