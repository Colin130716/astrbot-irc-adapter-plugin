#!/usr/bin/env python3
"""IRC适配器插件测试脚本"""
import sys
import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)

for path in (PARENT_DIR, BASE_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

def test_plugin_structure():
    """测试插件结构"""
    print("=" * 60)
    print("IRC适配器插件结构测试")
    print("=" * 60)
    
    # 检查必需文件
    required_files = [
        "metadata.yaml",
        "requirements.txt",
        "_conf_schema.json",
        "__init__.py",
        "main.py",
        "irc_adapter.py",
        "irc_event.py"
    ]
    
    print("\n📁 文件结构检查:")
    all_ok = True
    for file in required_files:
        file_path = os.path.join(BASE_DIR, file)
        if os.path.exists(file_path):
            print(f"  ✅ {file}")
        else:
            print(f"  ❌ {file} 缺失")
            all_ok = False
    
    # 检查文件内容
    print("\n📄 文件内容检查:")
    
    # 检查metadata.yaml
    try:
        import yaml
        with open(os.path.join(BASE_DIR, "metadata.yaml"), "r", encoding="utf-8") as f:
            metadata = yaml.safe_load(f)
        required_fields = ["name", "display_name", "version", "description", "support_platforms"]
        for field in required_fields:
            if field in metadata:
                print(f"  ✅ metadata.yaml: {field}")
            else:
                print(f"  ❌ metadata.yaml: {field} 缺失")
                all_ok = False
    except Exception as e:
        print(f"  ❌ metadata.yaml解析失败: {e}")
        all_ok = False
    
    # 检查requirements.txt
    if os.path.exists(os.path.join(BASE_DIR, "requirements.txt")):
        with open(os.path.join(BASE_DIR, "requirements.txt"), "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            print(f"  ✅ requirements.txt: {content}")
        else:
            print("  ⚠ requirements.txt为空")
    else:
        print("  ❌ requirements.txt缺失")
        all_ok = False
    
    # 检查配置schema
    try:
        with open(os.path.join(BASE_DIR, "_conf_schema.json"), "r", encoding="utf-8") as f:
            schema = json.load(f)
        if isinstance(schema, dict):
            if len(schema) > 0:
                print(f"  ✅ _conf_schema.json: {len(schema)}个配置项")
            else:
                print("  ✅ _conf_schema.json: 已清空，无插件级配置")
        else:
            print("  ❌ _conf_schema.json格式错误")
            all_ok = False
    except Exception as e:
        print(f"  ❌ _conf_schema.json解析失败: {e}")
        all_ok = False
    
    # 测试导入
    print("\n🔧 导入测试:")
    irc_available = False
    try:
        import irc  # noqa: F401
        irc_available = True
        print("  ✅ IRC依赖已安装")
    except Exception as e:
        print(f"  ⚠ IRC依赖未安装，跳过导入测试: {e}")

    if not irc_available:
        print("\n" + "=" * 60)
        if all_ok:
            print("🎉 结构检查通过。安装 `requirements.txt` 依赖后可继续验证导入。")
        else:
            print("⚠ 结构检查未全部通过，请先修复上述问题。")
        print("=" * 60)
        return

    try:
        from irc_adapter_plugin.main import IRCAdapterPlugin
        print("  ✅ 插件类导入成功")
    except Exception as e:
        print(f"  ❌ 插件类导入失败: {e}")
        all_ok = False
    
    try:
        from irc_adapter_plugin.irc_adapter import IRCPlatformAdapter
        print("  ✅ 适配器类导入成功")
    except Exception as e:
        print(f"  ❌ 适配器类导入失败: {e}")
        all_ok = False
    
    try:
        from irc_adapter_plugin.irc_event import IRCEvent
        print("  ✅ 事件类导入成功")
    except Exception as e:
        print(f"  ❌ 事件类导入失败: {e}")
        all_ok = False
    
    print("\n" + "=" * 60)
    if all_ok:
        print("🎉 所有检查通过！插件结构完整。")
        print("\n使用说明:")
        print("1. 将此目录复制到 AstrBot/data/plugins/")
        print("2. 安装 requirements.txt 中的依赖")
        print("3. 在 AstrBot WebUI 中添加一个 type=irc 的机器人")
        print("4. 填写服务器、昵称和频道配置后启动")
    else:
        print("⚠ 发现一些问题，请修复后再使用。")
    print("=" * 60)

if __name__ == "__main__":
    test_plugin_structure()
