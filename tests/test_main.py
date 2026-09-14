"""``main._build_modules`` 的装配逻辑本身——同 ``be-shell-go``
``cmd/shell/main_test.go`` 的判断，用手写夹具覆盖"解析 SHELL_CONFIG_JSON"/
"按 BRICKKIT_SERVED_MEMBERS 筛成员的几种状态"，不需要真实基础设施
（不连 DB/NATS，``_build_modules`` 本身也不连）。

阶段四附加 Task 0.2/0.3：SHELL_ENV_JSON（产出 7）整体退休，
BRICKKIT_SERVED_MEMBERS 取代它成为"这次谁真的被收编"的数据源，模块
自己的 configSchema 值改由 SHELL_CONFIG_JSON 的 config 字段直接携带。

阶段四附加 Task 0.4：SHELL_CONFIG_JSON 从"文件路径"改成"内容本身"——
值直接是这一个外壳自己的 modules 数组（be-ops shell-config --shell
<name> 打印出来的那一行），不再需要按 SHELL_NAME 从多个外壳里挑一个，
_build_modules 因此不再接收 shell_name 参数。
"""

from __future__ import annotations

import json

import pytest

import main


def test_已知的唯一真实组件都在MODULE_REGISTRY里() -> None:
    main._register_known_modules()
    assert set(main._MODULE_REGISTRY) == {"infra/print"}
    assert callable(main._MODULE_REGISTRY["infra/print"])


def test_解析SHELL_CONFIG_JSON并按BRICKKIT_SERVED_MEMBERS筛一遍(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SHELL_CONFIG_JSON",
        json.dumps(
            [
                {
                    "componentId": "infra/print",
                    "version": "1.0.5",
                    "schema": "infra_print",
                    "httpPort": 8400,
                    "extraPorts": {"grpc": 9400},
                    "config": {"COMPONENT_ID": "infra/print"},
                }
            ]
        ),
    )
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS", "infra-print-1-0-5")

    specs = main._build_modules()

    assert len(specs) == 1
    spec = specs[0]
    assert spec.component_id == "infra/print"
    assert spec.component_version == "1.0.5"
    assert spec.http_port == 8400
    assert spec.extra_ports == {"grpc": 9400}
    assert spec.schema == "infra_print"
    assert spec.env == {"COMPONENT_ID": "infra/print"}
    assert callable(spec.new_module)


def test_BRICKKIT_SERVED_MEMBERS未列出的成员被跳过不报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SHELL_CONFIG_JSON",
        json.dumps([{"componentId": "infra/print", "version": "1.0.5", "schema": "infra_print", "httpPort": 8400}]),
    )
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS", "")

    specs = main._build_modules()

    assert specs == []


def test_BRICKKIT_SERVED_MEMBERS里有SHELL_CONFIG_JSON找不到的成员时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SHELL_CONFIG_JSON",
        json.dumps([{"componentId": "infra/print", "version": "1.0.5", "schema": "infra_print", "httpPort": 8400}]),
    )
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS", "infra-print-1-0-5,mdm-customer-1-0-7")

    with pytest.raises(RuntimeError, match="BRICKKIT_SERVED_MEMBERS"):
        main._build_modules()


def test_BRICKKIT_SERVED_MEMBERS未设置时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL_CONFIG_JSON", json.dumps([]))
    monkeypatch.delenv("BRICKKIT_SERVED_MEMBERS", raising=False)

    with pytest.raises(RuntimeError, match="BRICKKIT_SERVED_MEMBERS"):
        main._build_modules()


def test_SHELL_CONFIG_JSON未设置时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHELL_CONFIG_JSON", raising=False)
    monkeypatch.delenv("BRICKKIT_SERVED_MEMBERS", raising=False)
    with pytest.raises(RuntimeError, match="SHELL_CONFIG_JSON"):
        main._build_modules()


def test_SHELL_CONFIG_JSON不是合法JSON时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL_CONFIG_JSON", "不是 JSON")
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS", "")
    with pytest.raises(RuntimeError, match="解析 SHELL_CONFIG_JSON 失败"):
        main._build_modules()
