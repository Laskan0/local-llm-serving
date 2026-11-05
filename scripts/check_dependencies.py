"""Проверка полного совпадения установленных Python-пакетов с файлом зависимостей."""

import importlib.metadata
import re
import sys
from pathlib import Path


def normalized(name):
    """Нормализовать имя Python-пакета для сравнения.

    :param name: Исходное имя пакета.
    :return: Имя в нижнем регистре с унифицированными разделителями.
    :rtype: str
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    """Сопоставить окружение с закреплёнными зависимостями.

    :raises SystemExit: При некорректном файле или несовпадении пакетов.
    """
    expected = {}
    for line in Path(sys.argv[1]).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "--hash=")):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;\\]+)(?:\s*\\)?", line)
        if not match:
            raise SystemExit("Unsupported or unpinned requirement in lock file")
        name, version = match.groups()
        key = normalized(name)
        if key in expected:
            raise SystemExit(f"Duplicate dependency: {key}")
        expected[key] = version
    if not expected:
        raise SystemExit("Empty dependency lock file")
    actual = {}
    for dist in importlib.metadata.distributions():
        key = normalized(dist.metadata["Name"])
        if key in actual:
            raise SystemExit(f"Duplicate installed distribution: {key}")
        actual[key] = dist.version
    differences = [
        f"{name}: expected={expected.get(name, 'absent')}, installed={actual.get(name, 'absent')}"
        for name in sorted(expected.keys() | actual.keys())
        if expected.get(name) != actual.get(name)
    ]
    if differences:
        raise SystemExit("Dependency lock mismatch:\n" + "\n".join(differences))
    print(f"dependency_lock_ok packages={len(expected)}", flush=True)


if __name__ == "__main__":
    main()
