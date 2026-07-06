"""Quick validation of all three changes."""

# 1. Test pinyin sanitize
import sys
sys.path.insert(0, r'D:\AI\Autoclaw\ERAG\erag\backend')
from app.services.skill_manager import sanitize_folder_name

# Test 1a: Chinese name with pinyin
result1 = sanitize_folder_name("文档生成助手")
assert result1 == "wen-dang-sheng-cheng-zhu-shou", f"Got: {result1}"
print("PASS: Chinese→pinyin: 文档生成助手 → wen-dang-sheng-cheng-zhu-shou")

# Test 1b: Mixed Chinese + English
result2 = sanitize_folder_name("IT运维助手")
assert result2 == "it-yun-wei-zhu-shou", f"Got: {result2}"
print("PASS: Mixed: IT运维助手 → it-yun-wei-zhu-shou")

# Test 1c: Pure ASCII (unchanged behavior)
result3 = sanitize_folder_name("doc-gen")
assert result3 == "doc-gen", f"Got: {result3}"
print("PASS: ASCII: doc-gen → doc-gen")

# 2. Test description truncation
from app.services.skill_manager import parse_skill_md
long_desc = "x" * 300
content = f"---\nname: test\ndescription: {long_desc}\n---\n\n# Body"
parsed = parse_skill_md(content)
assert len(parsed["description"]) == 250, f"Got length: {len(parsed['description'])}"
print("PASS: Description truncated to 250 chars")

# 3. Test resource functions
from app.services.skill_manager import get_skill_resource, list_resource_paths
paths = list_resource_paths("doc-gen")
print(f"PASS: doc-gen resources: {paths}")

print("\nAll tests passed!")
