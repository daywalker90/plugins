import subprocess
from collections import namedtuple
from pathlib import Path, PosixPath

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.9+
from typing import Generator, List

Plugin = namedtuple(
    "Plugin",
    [
        "name",
        "path",
        "language",
        "framework",
        "testfiles",
        "details",
    ],
)

exclude = [
    ".ci",
    ".git",
    ".github",
    "archived",
    "lightning",
]

override_subdirectory = {"watchtower-client": "watchtower-plugin"}


def configure_git():
    # Git needs some user and email to be configured in order to work in the context of GitHub Actions.
    subprocess.run(
        ["git", "config", "--global", "user.email", '"lightningd@github.plugins.repo"']
    )
    subprocess.run(["git", "config", "--global", "user.name", '"lightningd"'])


def get_testfiles(p: Path) -> List[PosixPath]:
    test_files = []
    for x in p.iterdir():
        if x.is_dir() and x.name == "tests":
            test_files.extend(
                [
                    y
                    for y in x.iterdir()
                    if y.is_file()
                    and y.name.startswith("test_")
                    and y.name.endswith(".py")
                ]
            )
        elif x.is_file() and x.name.startswith("test_") and x.name.endswith(".py"):
            test_files.append(x)
    return test_files


def list_plugins(plugins: list) -> str:
    return ", ".join([p.name for p, _ in sorted(plugins)])


def enumerate_plugins(basedir: Path) -> Generator[Plugin, None, None]:
    plugins = []
    for plugin_dir in basedir.iterdir():
        if not plugin_dir.is_dir() or plugin_dir.name in exclude:
            continue

        subdir = override_subdirectory.get(plugin_dir.name)
        if subdir:
            override_path = plugin_dir / subdir
            if override_path.is_dir():
                plugins.append(override_path)
            else:
                plugins.append(plugin_dir)
        else:
            plugins.append(plugin_dir)

    pip_pytest = [
        (x, find_framework_file(x, "requirements.txt"))
        for x in plugins
        if find_framework_file(x, "requirements.txt")
    ]
    print(f"Pip test framework plugins: {list_plugins(pip_pytest)}")

    uv_pytest = [
        (x, find_framework_file(x, "uv.lock"))
        for x in plugins
        if find_framework_file(x, "uv.lock")
    ]

    # Don't double detect plugins migrating to uv
    poetry_pytest = [
        (x, find_framework_file(x, "poetry.lock"))
        for x in plugins
        if find_framework_file(x, "poetry.lock") and x not in [p for p, _ in uv_pytest]
    ]

    for plugin in plugins:
        pyproject = find_framework_file(plugin, "pyproject.toml")
        if not pyproject:
            continue

        already_uv = any(p == plugin for p, _ in uv_pytest)
        already_poetry = any(p == plugin for p, _ in poetry_pytest)

        if already_uv or already_poetry:
            continue

        framework = detect_pyproject_framework(pyproject)
        if framework == "uv":
            uv_pytest.append((plugin, pyproject))
        elif framework == "poetry":
            poetry_pytest.append((plugin, pyproject))
        else:
            print(f"Unsupported framework {framework} in {plugin}")

    print(f"Uv test framework plugins: {list_plugins(uv_pytest)}")
    print(f"Poetry test framework plugins: {list_plugins(poetry_pytest)}")

    for p, req_path in sorted(pip_pytest):
        yield Plugin(
            name=p.name,
            path=p,
            language="python",
            framework="pip",
            testfiles=get_testfiles(p),
            details={
                "requirements": req_path,
                "devrequirements": find_framework_file(p, "requirements-dev.txt"),
            },
        )

    for p, pyproject in sorted(poetry_pytest):
        yield Plugin(
            name=p.name,
            path=p,
            language="python",
            framework="poetry",
            testfiles=get_testfiles(p),
            details={
                "pyproject": pyproject,
            },
        )

    for p, pyproject in sorted(uv_pytest):
        yield Plugin(
            name=p.name,
            path=p,
            language="python",
            framework="uv",
            testfiles=get_testfiles(p),
            details={
                "pyproject": pyproject,
            },
        )


def find_framework_file(plugin: Path, filename: str) -> Path | None:
    tests_dir = plugin / "tests" / filename
    if tests_dir.exists():
        return tests_dir
    root_file = plugin / filename
    if root_file.exists():
        return root_file
    return None


def detect_pyproject_framework(pyproject: Path) -> str | None:
    if not pyproject.exists():
        return None

    with pyproject.open("rb") as f:
        data = tomllib.load(f)

    tool = data.get("tool", {})
    build_system = data.get("build-system", {})

    if "poetry" in tool:
        return "poetry"
    if "uv" in tool or build_system.get("requires", [None])[0] == "uv":
        return "uv"
    if "flit" in tool:
        return "flit"
    if "hatch" in tool:
        return "hatch"

    if build_system.get("build-backend") == "setuptools.build_meta":
        return "setuptools"

    return "unknown"
