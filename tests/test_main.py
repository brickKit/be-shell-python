"""``main._build_modules`` 的装配逻辑本身——同 ``be-shell-go``
``cmd/shell/main_test.go`` 的判断，用手写夹具覆盖"解析
BRICKKIT_SERVED_MEMBERS_CONFIG"的几种状态，不需要真实基础设施（不连
DB/NATS，``_build_modules`` 本身也不连）。

阶段四附加 Task 0.2/0.3：SHELL_ENV_JSON（产出 7）整体退休，
BRICKKIT_SERVED_MEMBERS 取代它成为"这次谁真的被收编"的数据源。

阶段四附加 Task 0.6（brickKit v0.4.2）：SHELL_CONFIG_JSON + ``be-ops
shell-config`` 那一整套机制整体退休，改成解析平台原生注入的
BRICKKIT_SERVED_MEMBERS_CONFIG——这份数据本身就已经是"这次真的被收编"
的成员集合（不需要再单独按 BRICKKIT_SERVED_MEMBERS 筛一遍），且
``config`` 键是原始 configSchema 驼峰 key，需要 ``_config_env_var_name``
转成 ``SCREAMING_SNAKE_CASE`` 才能被 ``besdk.Config`` 正确查到。
"""

from __future__ import annotations

import json

import pytest

import main


def test_已知的唯一真实组件都在MODULE_REGISTRY里() -> None:
    main._register_known_modules()
    assert set(main._MODULE_REGISTRY) == {"infra/print"}
    assert callable(main._MODULE_REGISTRY["infra/print"])


def test_解析真实数据形状装配模块(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BRICKKIT_SERVED_MEMBERS_CONFIG",
        json.dumps(
            [
                {
                    "componentId": "infra/print",
                    "version": "1.0.7",
                    "httpPort": 8400,
                    "extraPorts": [{"name": "grpc", "port": 9400}],
                    "config": {
                        "authzBundleUrl": "http://infra-authz-1-0-7:8223/authz/bundle",
                        "pgSchema": "infra_print",
                    },
                }
            ]
        ),
    )

    specs = main._build_modules()

    assert len(specs) == 1
    spec = specs[0]
    assert spec.component_id == "infra/print"
    assert spec.component_version == "1.0.7"
    assert spec.http_port == 8400
    # extraPorts 是数组形状（[{name,port}]），必须正确转成 dict。
    assert spec.extra_ports == {"grpc": 9400}
    # pgSchema 要单独抽成 schema 字段（原始驼峰 key，不经过大写下划线
    # 转换那条路径）。
    assert spec.schema == "infra_print"
    # config 键必须从原始驼峰形式转成 SCREAMING_SNAKE_CASE——besdk.Config
    # 内部按这个规则查找，转换漏了模块会真机 panic。
    assert spec.env == {
        "AUTHZ_BUNDLE_URL": "http://infra-authz-1-0-7:8223/authz/bundle",
        "PG_SCHEMA": "infra_print",
    }
    assert callable(spec.new_module)


def test_零成员时装出零模块(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS_CONFIG", json.dumps([]))

    specs = main._build_modules()

    assert specs == []


def test_变量未设置时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRICKKIT_SERVED_MEMBERS_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="BRICKKIT_SERVED_MEMBERS_CONFIG"):
        main._build_modules()


def test_不是合法JSON时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS_CONFIG", "不是 JSON")
    with pytest.raises(RuntimeError, match="解析 BRICKKIT_SERVED_MEMBERS_CONFIG 失败"):
        main._build_modules()


def test_成员在MODULE_REGISTRY里找不到时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BRICKKIT_SERVED_MEMBERS_CONFIG",
        json.dumps(
            [
                {
                    "componentId": "hrm/payroll",
                    "version": "1.0.0",
                    "httpPort": 8090,
                    "config": {"pgSchema": "hrm_payroll"},
                }
            ]
        ),
    )
    with pytest.raises(RuntimeError, match="_MODULE_REGISTRY"):
        main._build_modules()


def test_缺pgSchema时报错(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "BRICKKIT_SERVED_MEMBERS_CONFIG",
        json.dumps([{"componentId": "infra/print", "version": "1.0.7", "httpPort": 8400, "config": {}}]),
    )
    with pytest.raises(RuntimeError, match="pgSchema"):
        main._build_modules()


def test_docker_compose对VAR占位符做全文本替换撑坏JSON时能自愈(monkeypatch: pytest.MonkeyPatch) -> None:
    """判断逐一对应 ``be-shell-go`` 的
    ``TestBuildModules_docker_compose对VAR占位符做全文本替换撑坏JSON时能自愈``
    ——真机 ``brickkit up`` 复现出的真实 bug：docker compose 对整份生成好
    的 docker-compose.yaml 按纯文本做 ``${VAR}`` 替换，会把真实密钥的原始
    换行符直接拼进本该是单行 JSON 的字符串里、撑坏 JSON。本仓库目前唯一
    模块（infra/print）没有密钥类配置项，不会真的触发，但判断需要跟
    be-shell-go 保持一致。
    """
    broken = (
        '[{"componentId":"infra/print","version":"1.0.7","httpPort":8400,'
        '"config":{"pgSchema":"infra_print","someSecret":"-----BEGIN KEY-----\n'
        "line-two\n-----END KEY-----\"}}]"
    )
    monkeypatch.setenv("BRICKKIT_SERVED_MEMBERS_CONFIG", broken)

    specs = main._build_modules()

    assert len(specs) == 1
    assert specs[0].env["SOME_SECRET"] == "-----BEGIN KEY-----\nline-two\n-----END KEY-----"


def test_sanitize_served_members_config只转义字符串内部的裸控制字符() -> None:
    """单独钉住最容易出错的两个边界：字符串外部的裸换行（纯格式化空白）
    不能被误伤；字符串内部已转义的双引号不能扰乱状态机。"""
    raw = '[\n  {"a":"line1\nline2","b":"has \\"quote\\" then\ttab"}\n]'

    got = json.loads(main._sanitize_served_members_config(raw))

    assert got[0]["a"] == "line1\nline2"
    assert got[0]["b"] == 'has "quote" then\ttab'


@pytest.mark.parametrize(
    ("key", "want"),
    [
        ("pgSchema", "PG_SCHEMA"),
        ("authzBundleUrl", "AUTHZ_BUNDLE_URL"),
        ("defaultWarehouseId", "DEFAULT_WAREHOUSE_ID"),
        ("otelBaseUrl", "OTEL_BASE_URL"),
        ("appTokenSigningKeyPem", "APP_TOKEN_SIGNING_KEY_PEM"),
    ],
)
def test_config_env_var_name跟brickKit自己的EnvVarName算法逐字对应(key: str, want: str) -> None:
    assert main._config_env_var_name(key) == want
