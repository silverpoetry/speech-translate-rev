from __future__ import annotations

import importlib


def replace_function(file_path: str, old_function_name: str, new_function_code: str) -> bool:
    new_function_code = new_function_code.strip()
    with open(file_path, "r", encoding="utf-8") as file:
        file_content = file.read()

    start_index = file_content.find(f"def {old_function_name}(")
    if start_index == -1:
        return False

    signature_end = file_content.find("\n", start_index)
    old_function_end = file_content.find("\n\n", signature_end)
    if signature_end == -1 or old_function_end == -1:
        return False

    new_file_content = file_content[:start_index] + new_function_code + file_content[old_function_end:]
    with open(file_path, "w", encoding="utf-8") as file:
        file.write(new_file_content)
    return True


def main() -> None:
    try:
        skipfiles = importlib.import_module("torch._dynamo.skipfiles")
    except ModuleNotFoundError:
        print(">> torch._dynamo.skipfiles is not present; skipping legacy torch build patch")
        return

    print(">> Patching torch._dynamo.skipfiles for legacy build compatibility")
    patched = replace_function(
        skipfiles.__file__,
        "_module_dir",
        """
def _module_dir(m: types.ModuleType):
    try:
        return _strip_init_py(m.__file__)
    except AttributeError:
        return ""
""",
    )
    if not patched:
        print(">> torch._dynamo.skipfiles._module_dir was not found; skipping patch")


if __name__ == "__main__":
    main()
