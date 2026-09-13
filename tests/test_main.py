"""``main._build_modules`` 的装配逻辑本身——同 ``be-shell-go``
``cmd/shell/main_test.go`` 的判断，用手写夹具覆盖"按外壳挑模块"/"外壳
不存在时报错"/"外壳还没原子式切换完时报错"，不需要真实基础设施
（不连 DB/NATS，``_build_modules`` 本身也不连）。
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


def test_按SHELL_NAME挑出对应外壳并拼出真实ModuleSpec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
                    }
                ],
            }
        ],
    )
    env_path = _write_json(
        tmp_path / "shell-env.json",
        [
            {
                "Name": "py-render",
                "Modules": [{"ComponentID": "infra/print", "Env": {"COMPONENT_ID": "infra/print"}}],
            }
        ],
    )
    monkeypatch.setenv("SHELL_CONFIG_JSON", config_path)
    monkeypatch.setenv("SHELL_ENV_JSON", env_path)

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


def test_外壳在shell_config里不存在时报错(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _write_json(tmp_path / "shell-config.json", [{"name": "py-render", "modules": []}])
    env_path = _write_json(tmp_path / "shell-env.json", [{"Name": "py-render", "Modules": []}])
    monkeypatch.setenv("SHELL_CONFIG_JSON", config_path)
    monkeypatch.setenv("SHELL_ENV_JSON", env_path)

    with pytest.raises(RuntimeError, match="shell-config.json"):
        main._build_modules("go-infra")


def test_外壳还没原子式切换完时报错(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """py-render 在产出 4（shell-config.json）里存在（那份数据不看
    local: true），但还没原子式切换完时，产出 7（shell-env.json）里会
    完全没有这个外壳（同 be-ops shell-env 的既有行为：整个外壳静默
    跳过，不产出半份数据）——这里必须报错，不能装出一批没有真实 env
    的模块。
    """
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
    env_path = _write_json(tmp_path / "shell-env.json", [])
    monkeypatch.setenv("SHELL_CONFIG_JSON", config_path)
    monkeypatch.setenv("SHELL_ENV_JSON", env_path)

    with pytest.raises(RuntimeError, match="shell-env.json"):
        main._build_modules("py-render")


def test_未设置环境变量时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHELL_CONFIG_JSON", raising=False)
    monkeypatch.delenv("SHELL_ENV_JSON", raising=False)
    with pytest.raises(RuntimeError, match="SHELL_CONFIG_JSON"):
        main._build_modules("py-render")
