import sys
import os

VERSION_FILE = os.path.join(os.path.dirname(__file__), "version.txt")


def read_version():
    with open(VERSION_FILE, "r") as f:
        return f.read().strip()


def bump(version: str, level: str = "patch") -> str:
    parts = version.split(".")
    while len(parts) < 3:
        parts.append("0")

    if level == "major":
        parts[0] = str(int(parts[0]) + 1)
        parts[1] = "0"
        parts[2] = "0"
        parts = parts[:3]
    elif level == "minor":
        parts[1] = str(int(parts[1]) + 1)
        parts[2] = "0"
        parts = parts[:3]
    elif level == "patch":
        parts[2] = str(int(parts[2]) + 1)
        parts = parts[:3]
    elif level == "micro":
        if len(parts) < 4:
            parts.append("1")
        else:
            parts[3] = str(int(parts[3]) + 1)

    return ".".join(parts)


def write_version(version: str):
    with open(VERSION_FILE, "w") as f:
        f.write(version + "\n")


if __name__ == "__main__":
    level = sys.argv[1] if len(sys.argv) > 1 else "patch"
    current = read_version()
    new = bump(current, level)
    write_version(new)
    print(f"{current} -> {new}")
