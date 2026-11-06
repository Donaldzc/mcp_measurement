# -*- coding: utf-8 -*-
"""一次性 setup: 把 GitHub PAT 写入 .env.local。跑一次即可。
命令: python setup_token.py ghp_你的token值
之后所有脚本自动从 .env.local 加载，不需要再次传 token。
"""
import sys, pathlib
if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)
token = sys.argv[1].strip().strip('"').strip("'")
p = pathlib.Path(__file__).resolve().parent / ".env.local"
p.write_text(f'GITHUB_TOKEN="{token}"\n', encoding="utf-8")
print(f"token 已写入 {p}")
print("建议: git add .gitignore（.env.local 已被忽略）")
