# FreeCM Dependency Refactor TODO

这份文档记录本仓库依赖管理重构的目标、完成状态和下游接线要求。目标是把 FreeCM 的依赖管理能力从历史上的
`depsfixture`、`cpprepomgr`、`swiftrepomgr` 边界里剥离出来，形成真正跨语言、跨仓库可复用的
`freecm` core；语言和构建系统相关能力只作为 adapter 存在。

核心原则必须守住：

- 只有 `--init` 可以联网。
- `--update` 只做本地物化，不得联网。
- 除 `--init` 外，任何命令都不得 clone、fetch、download 或访问远端资源。

## 目标形态

- `freecm` 是正式的跨语言依赖管理核心包。
  - 管理 lock/schema、seed repositories、materialized roots、manual/pinned/latest 模式。
  - 管理 asset seeds、path maps、terminal style、通用 source-root workflow script。
  - 提供稳定的通用 CLI 和库 API，供所有下游仓库接线。
- `cpprepomgr` 只保留 C++/CMake 专属能力。
  - CMake presets。
  - CMake dependency build。
  - package tools。
  - C++/Qt/repo-tool 里确实只服务 C++ 仓库的辅助工具。
- `swiftrepomgr` 只保留 Swift/Xcode 专属能力。
  - `SwiftConfigs`。
  - Xcode 本地工程生成和 setup callback。
  - Swift/Xcode 所需 extra source-root path adapter。
- `depsfixture` 不再作为正式业务命名空间。
  - 现有核心能力迁入 `freecm`。
  - 旧路径不保留长期 shim。

## 不保留的历史包袱

- 非 C++ 仓库不得再 import `cpprepomgr` 的通用 workflow。
- Swift/Xcode adapter 不得依赖 `cpprepomgr`。
- Android、Go、混合仓库不得因为使用 FreeCM 依赖管理而看到 C++ 命名空间。
- core 默认 required paths 不再是 `CMakeLists.txt`。
- 通用依赖状态不再命名为 Swift source roots 或 C++ dependency roots。
- 不为了旧 import 路径长期双写实现。短期 shim 只服务仓库内迁移窗口，最后删除。

## Phase 1: 建立 `freecm` Core（已完成）

- 新增 `freecm` Python package。
- 从 `depsfixture` 迁移通用模块：
  - dependency roots / lock schema / JSONC loader。
  - asset seeds。
  - git repository helpers。
  - path maps。
  - terminal style。
- 从 `swiftrepomgr.source_root_workflow` 迁移通用 `SourceRootWorkflowScript`。
- 保留 host-owned 通用入口：
  - `python3 configs/source_root_workflow.py --init`
  - `python3 configs/source_root_workflow.py --update`
  - `python3 configs/source_roots.py materialize`
  - `python3 configs/source_roots.py verify`
  - `python3 -m freecm.dependency_roots --help`
- `pyproject.toml` 增加 `freecm` package。
- core 层默认 `required_relative_paths` 为空；语言 adapter 或下游配置自己声明要求。
- 仓库内代码不再 import `depsfixture`。

## Phase 2: 切开 C++/CMake Adapter（已完成）

- 把 `cpprepomgr.cmake_workflow` 中的通用依赖 workflow 移出到 `freecm`。
- `cpprepomgr` 保留并明确命名 C++ 专属内容：
  - `CMakeDependencyBuildSpec`。
  - CMake dependency build order/context。
  - CMake preset generation。
  - CMake module/package data。
  - packaging CLI。
- `cpprepomgr.source_root_workflow` 不再是通用入口。
  - 如果保留，必须只是 C++ adapter 或短期 shim。
  - 新下游不得使用它做通用 dependency workflow。
- C++ tests 只验证 C++ adapter 行为，不覆盖 core 的职责。

## Phase 3: 切开 Swift/Xcode Adapter（已完成）

- `swiftrepomgr` 只暴露 Swift/Xcode adapter。
- `SwiftConfigs`、build settings、commerce policy、Xcode local setup callback 继续留在 `swiftrepomgr`。
- `SourceRootWorkflow` 如果仍保留在 `swiftrepomgr`，必须只是对 `freecm` core 的 Swift adapter 包装。
- `SourceRootWorkflowScript` 迁到 `freecm`，Swift 只传入 Swift/Xcode callback。
- `swiftrepomgr` 不得 import `cpprepomgr`。

## Phase 4: 删除旧公共入口（已完成）

- 下游和仓库内 tests 全部迁到 `freecm` import 后，删除长期 shim。
- 删除或降级以下旧公共入口：
  - `depsfixture.dependency_roots`
  - `depsfixture.asset_seeds`
  - `depsfixture.path_maps`
  - `depsfixture.terminal_style`
  - `cpprepomgr.cmake_workflow` 中的通用 API
  - `swiftrepomgr.source_root_workflow` 中的通用脚本 API
- 错误提示和 Usage 文档统一指向 `configs/source_root_workflow.py --init|--update`
  或新的 `freecm` core module，不再推荐 `cpprepomgr` 入口。
- 清理 README、tests、VS Code extension 文案里的旧命名。

本仓库旧 shim 已删除，不保留 `depsfixture` 或 `swiftrepomgr.source_root_workflow` 公共入口。

## Phase 5: 下游接线（待用户手动处理）

下游仓库由使用者稍后手动接线；本仓库重构已经提供清晰的新 API 和迁移目标。

优先迁移这些仓库：

- `/Users/henrykang/Documents/AstroformNetwork`
- `/Users/henrykang/Documents/PcbAndroid`
- `/Users/henrykang/Documents/PcbAtlas`
- `/Users/henrykang/Documents/GeoToy`

每个下游迁移后的目标：

- `configs/source_roots.py` 只 import `freecm` core 或语言 adapter。
- 非 C++ 仓库不再 import `cpprepomgr`。
- Swift/Xcode 仓库通过 `swiftrepomgr` adapter 接入，但依赖核心来自 `freecm`。
- C++/CMake 仓库通过 `cpprepomgr` adapter 使用 CMake 专属能力，但 lock/materialize 核心来自 `freecm`。
- `source_roots.lock.jsonc.in` schema 不因迁移无意义 churn。

## 网络边界

这是硬约束，不是建议。

- `--init` 是唯一联网入口。
  - 可以 clone missing seed repositories。
  - 可以 fetch existing seed repositories。
  - 可以 download/prepare asset seeds。
  - 可以刷新递归 dependency seed closure。
- `--update` 必须离线。
  - 只读取 active lock。
  - 只从本地 seed/materialized state 物化。
  - 不得 clone。
  - 不得 fetch。
  - 不得 download。
  - 缺少本地 commit 或 asset 时直接失败，并提示先运行 `--init`。
- 其他命令必须离线：
  - `materialize`
  - `verify`
  - `status`
  - VS Code lock-mode controls
  - repo commands validator
  - 任何 read-only diagnostic command
- 所有联网函数必须能在测试中被集中 mock 或替换。
- 测试必须证明非 `--init` 路径不会调用 clone/fetch/download。

## 测试要求（已通过）

本仓库重构收口时必须保持这些验证通过：

```bash
python3 -m compileall -q freecm cpprepomgr swiftrepomgr tools hooks tests
python3 -m unittest discover -s tests -v
cd vscode-extension
npm test
npm audit --omit=optional
cd ..
git diff --check
```

已新增或更新测试覆盖：

- `--init` 允许 seed clone/fetch 和 asset download。
- `--update` 强制 `allow_network=False`。
- 所有非 `--init` CLI 子命令禁止 fetch/clone/download。
- missing local commit 在离线路径下失败，并提示运行 `--init`。
- missing asset 在离线路径下失败，并提示运行 `--init`。
- C++ adapter 不被 Swift/Android/Go 下游 import。
- Swift adapter 只依赖 `freecm` core，不依赖 `cpprepomgr`。
- core 默认 required paths 为空，不再暗含 `CMakeLists.txt`。
- 下游接线后需要继续证明示例配置可用新 API 完成 lock、materialize、verify。

## 本仓库完成状态

- 新代码路径以 `freecm` core 为依赖管理唯一正式入口。
- C++ 和 Swift 包只承担各自语言/构建系统 adapter 职责。
- 旧 shim 已删除。
- 除 `--init` 外，没有任何命令可能联网。
- README、Usage、错误提示和测试名称不再把通用依赖管理称为 C++ 或 Swift 专属能力。

下游完成标准仍然是：AstroformNetwork、PcbAndroid、PcbAtlas、GeoToy 都能按新 API 接线。
