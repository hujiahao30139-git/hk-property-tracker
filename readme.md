# HK PropPulse v5.0 · 香港楼市四大中介真实笋盘严选与近1月宏观预测终端

专为个人楼市投资研究打造的零成本自动化数据看板，100% 聚合 **中原地产 (Centaline)**、**美联物业 (Midland)**、**28Hse 香港屋网**、**House730** 四大中介真实在售/成交盘源，并内置**近1个月宏观财经新闻分析与 3个月/半年/1年楼价预测模型**。支持部署在 **Google Sites（协作平台）** 或 **GitHub Pages**。

## 1. 在 Google Sites（协作平台）实现相同效果的两种方法

### 方法 A：整页嵌入 HTML 代码（无需配置 GitHub Pages 也能独立运行，30秒完成）
1. 在画布（Canvas）中打开 `index.html` 预览，切换到 **「4. 🚀 Google Sites / GitHub 部署」** 板块，点击 **「📋 一键复制完整 HTML 代码」**。
2. 打开 `sites.google.com` 并新建一个空白网站。
3. 在右侧工具栏点击 **「页面 (Pages)」→ 将鼠标移到底部「+」号 → 点击「整页嵌入 (Full page embed)」**，命名为「香港楼市研究终端」。
4. 点击页面中央的 **「添加嵌入内容 (Add embed)」→ 切换到「嵌入代码 (Embed code)」**，将刚刚复制的 `index.html` 完整代码粘贴进去，点击 **「下一步 (Next)」→「插入 (Insert)」**。
5. 点击右上角 **「发布 (Publish)」**（可在右上角共享设置中设为**仅自己可见 Restricted**，保护个人私密研究）。

### 方法 B：在 Google Sites 中同时实现「每日后台自动抓取更新」
- **方式 1（Google Sites + GitHub 自动数据源）**：保留你的 GitHub 仓库每天自动运行 `scraper.py` 更新 `market_data.json`，然后在 Google Sites 网页的 **「4. 🚀 Google Sites / GitHub 部署」** 板块中，将你的 GitHub Raw 数据地址（格式为 `https://raw.githubusercontent.com/你的用户名/仓库名/main/market_data.json`）填入 **「跨平台远程数据源 URL 配置」** 并点击保存。
- **方式 2（Google Sites 按网址嵌入）**：在 Google Sites 选择 **「嵌入 → 按网址 (By URL)」** 填入你的 GitHub Pages 网址。