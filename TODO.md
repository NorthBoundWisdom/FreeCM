# FreeCM 架构健康度与重构 TODO (D:\FreeCM)

## 背景介绍
FreeCM 作为一个跨语言的依赖管理核心（Core），被设计为提供基础的包管理和环境隔离能力，而具体的项目领域逻辑（C++ CMake、Swift Xcode、Android Gradle 等）则交由各自的适配器包（`repomgrcpp`, `repomgrswift`, `repomgrandroid` 等）来实现。

经过对 `D:\FreeCM` 最新代码的全面审计，我们发现先前的硬编码域泄漏问题（如在核心模型中硬编码 `geo2dcore_dependency_root`）已经被清理干净。然而，随着代码的不断演进和多语言适配器的增加，当前代码库中依然存在大量**“复制粘贴”式的冗余代码**、**旧版废弃术语的遗留**以及**包边界不清晰**的问题。

如果您打算手动在新的窗口或 IDE 中逐步修复这些问题，本指南为您提供了一份极其详细的重构清单与背景说明，帮助您有条不紊地完成此次架构清理。

---

## 详细重构清单

### 1. 核心包能力补全与去重 (`freecm/`)
**背景**：各个适配器和工具脚本中大量使用了 `git rev-parse --show-toplevel` 来寻找 Git 根目录，也各自实现了一套打印执行日志的子进程调用包装器。这导致了严重的逻辑重复。此外，Python 3.10+ 标准库已有的 `Path.is_relative_to()` 在很多老脚本中被手动实现。

- [ ] **1.1 统一 Git 根目录获取**
  - **任务**：在 `freecm/git_repositories.py` 的末尾新增一个通用方法：
    ```python
    def git_toplevel(cwd: Path) -> Path:
        completed = run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
        )
        return Path(completed.stdout.strip()).resolve()
    ```
  - **后续替换**：查找全局代码中的 `['git', 'rev-parse', '--show-toplevel']`，全部替换为调用此方法。
  
- [ ] **1.2 统一带日志输出的命令执行器**
  - **任务**：新建文件 `freecm/subprocess_utils.py`，实现 `run_logged_command`：
    ```python
    import subprocess
    from pathlib import Path
    from typing import Sequence

    def run_logged_command(
        cmd: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
        prefix: str = ">> ",
    ) -> subprocess.CompletedProcess[str]:
        print(f"{prefix}{' '.join(str(c) for c in cmd)}")
        return subprocess.run(
            [str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env, check=check, text=True
        )
    ```
  
- [ ] **1.3 统一类型定义**
  - **任务**：新建 `freecm/utils.py`，存放共享类型：
    ```python
    from pathlib import Path
    from typing import Union
    PathValue = Union[str, Path]
    ```

### 2. 适配器包清理 (`repomgr*`)
**背景**：适配器包在早期快速开发时，沿用了一些老旧的名称（如 `SourceRoot`），同时也复制了大量基础设施代码（如打印颜色日志、执行终端命令等）。

- [ ] **2.1 `repomgrswift` 全面术语对齐**
  - **背景**：`freecm` 核心早已将概念统一为 `DependencyRoot`，但 Swift 适配器中依然满天飞 `SourceRoot`。
  - **任务**：在 `repomgrswift/source_roots.py` 中：
    - 将类 `ResolvedSourceRoots` 重命名为 `ResolvedSwiftDependencyRoots`（防止与 freecm 核心类重名）。
    - 将别名 `SourceRootDependencySpec = DependencyRootSpec` 删除，直接使用 `DependencyRootSpec`。
    - 将 `SourceRootWorkflowConfig` 重命名为 `DependencyRootWorkflowConfig`。
    - 将 `SourceRootWorkflow` 重命名为 `DependencyRootWorkflow`。
    - 检查并修改 `repomgrswift/__init__.py` 中的 `__all__` 导出列表以匹配上述修改。

- [ ] **2.2 C++ 适配器 (`repomgrcpp`) 的违规私有 API 修复**
  - **任务**：在 `repomgrcpp/cmake_workflow.py` 的顶部导入中，将 `_stderr_supports_color` 和 `_stdout_supports_color` 替换为公共版本 `stderr_supports_color` 和 `stdout_supports_color`。
  - **任务**：将 `REPOCONFIGSMGR_DEBUG` 环境变量统一更名为当前的 `FREECM_DEBUG`。

- [ ] **2.3 清理错误的模块重新导出**
  - **背景**：`repomgrcpp/tools/__init__.py` 错误地重新导出了大量泛用的跨语言工具（如 `collect_empty_dirs`, `remove_empty_dirs`），这破坏了 `repomgrcpp` 只关注 C++ 的包边界原则。
  - **任务**：打开 `repomgrcpp/tools/__init__.py`，删除掉对 `tools.*` 下泛用函数的导入和重新导出，只保留 `CPP_EXTENSIONS` 等 C++ 专属工具。

- [ ] **2.4 各适配器复用核心执行器**
  - **任务**：在 `repomgrandroid/workflow.py` 中删除 `default_command_runner`；在 `repomgrdotnet/workflow.py` 中删除私有的 `run_command`。将它们全部改为调用第一步中创建的 `freecm.subprocess_utils.run_logged_command`。

### 3. 脚本与 Hook 冗余代码消除 (`tools/` & `hooks/`)
**背景**：在多个独立脚本中，存在大量老旧的兼容性代码和样板代码。

- [ ] **3.1 删除手写的 `is_relative_to`**
  - **背景**：在 Python 3.9 之前不支持 `Path.is_relative_to`，所以代码中手写了兼容版本。既然现在项目要求 Python 3.10+，这些可以全部干掉。
  - **任务**：在以下文件中搜索并删除 `is_relative_to(path, base)` 函数，直接改为调用标准库的 `path.is_relative_to(base)`：
    - `hooks/pre_commit.py`
    - `repomgrcpp/package/common.py`
    - `tools/host_clang_format.py`
    - `tools/remove_old_build.py`

- [ ] **3.2 替换 `git_toplevel` 调用**
  - **任务**：在 `hooks/install.py`, `hooks/pre_commit.py`, `tools/host_clang_format.py`, `tools/remove_old_build.py`, `repomgrcpp/cmake_workflow.py` 这 5 个文件中，将手写的 `subprocess.run(["git", "rev-parse", "--show-toplevel"])` 替换为调用 `freecm.git_repositories.git_toplevel(Path.cwd())`。

### 4. 彻底删除废弃文件
- [ ] **4.1 删除 `hooks/format.py`**
  - **背景**：这个文件是一个只针对 C++ 的旧版内部 Hook 封装，其功能早就被 `pre_commit.py` 中的多语言格式化功能完全取代。
  - **任务**：直接安全删除该文件，不会影响任何现代工作流。

---

## 验收标准 (Verification Plan)
在您完成上述修改后，建议运行以下命令以确保重构没有破坏现有逻辑：

1. **语法与导入检查**：
   ```bash
   python -m compileall -q freecm repomgrcpp repomgrswift repomgrandroid repomgrdotnet tools hooks tests
   ```
2. **单元测试回归**：
   ```bash
   python -m unittest discover -s tests -v
   ```
3. **VS Code 插件校验**：
   ```bash
   cd vscode-extension
   npm test
   ```
