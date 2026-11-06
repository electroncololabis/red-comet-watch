# 赤色彗星补给战情室

面向万代模型收藏者的 Windows 本地价格观察站。支持闲鱼和拼多多的关注搜索、当前价与历史最低价、日元发售价换算，以及合并后的最低三件商品。

## 从源码运行

需要 Windows 11 x64、Python 3.12 和 Microsoft Edge 或 Google Chrome。下列示例使用 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --locked --python 3.12
uv run uvicorn hguc_watch.app:app --host 127.0.0.1 --port 8765
```

浏览器打开 <http://127.0.0.1:8765>。在面板里分别登录闲鱼和拼多多，添加关注型号，再点“搜索全部关注”。在“自动任务”中部署后，Windows 才会按设置运行搜索和打开面板。

源码模式把数据库、日志和两个平台各自的浏览器登录配置保存在项目的 `data/` 中。首次运行会生成该目录。请把它当作私人数据保管。退出源码版后台可在启动服务的终端按 Ctrl+C。

## 价格和筛选

- 按关注名称、型号、版本和别名筛选。明确标注“旧 HG”或“老 HG”的商品会被排除。“删除并屏蔽”可永久排除单条商品，支持恢复。
- 排名优先使用已核对的含运费价格。运费或规格不明的结果单列。“非偏远包邮”按非偏远地区估算，其他运费以平台按账号地址显示的金额为准。
- 每次搜索开始时更新 JPY/CNY 汇率，并换算已填写日元发售价的型号。实际成交价可在“标记已购入”时录入。
- 搜索覆盖平台页面返回的有限结果。平台需要重新登录或验证时，按该平台提示处理。

## 测试和打包

```powershell
uv run pytest
uv run python packaging/build_windows.py --iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

打包要求 Windows x64、Microsoft .NET Framework 4.x 的 C# 编译器以及 Inno Setup。将命令中的路径换成实际安装的 `ISCC.exe`。输出在本地 `dist/`。安装版使用随附的私有 Python，以当前用户权限安装，数据位于 `%LOCALAPPDATA%\RedCometWatch\data`。卸载时可选择保留或清理默认目录下的应用数据。

## 许可证

源码、文档和仓库内的抽象界面图形采用 [MIT 许可证](LICENSE)。项目独立于万代、闲鱼和拼多多。文中品牌和型号名称用于识别商品，其权利归各自权利人。
