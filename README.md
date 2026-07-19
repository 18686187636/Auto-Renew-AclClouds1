## 🚀 AclClouds 自动续期

这是一个基于 GitHub Actions 的自动化脚本，用于定时登录自动续期[AclCloud](https://dash.aclclouds.com) 应用。

⚠️ 有cf盾,太垃圾的机房节点可能过不了，建议用稍微干净点的节点  [B2proxy住宅代理](https://www.b2proxy.com/signup?code=0F5133)


在仓库 `Settings → Secrets and variables → Actions` 中添加以下 Secrets：

| Secret 名称 | 是否必填 | 说明 | 示例 |
|---|---|---|---|
| `EMAIL`         | ✅必填 | Aclclouds 邮箱 |
| `PASSWORD`      | ✅必填 | Aclclouds 密码 |
| `NODE_LINK`     | ❌可选 | 代理节点地址,例如:vless:// vmess:// trojan:// hysteria2:// anytls://|
| `TG_BOT_TOKEN`  | ❌可选 | Telegram Bot Token | 
| `TG_CHAT_ID`    | ❌可选 | Telegram Chat ID |


### 代理格式（确认在v2rayN里使用正常的节点,使用注册时使用的代理节点）

`NODE_LINK` 支持以下任意一种代理协议的完整分享链接（不配置则直连）：

- **VLESS**：`vless://uuid@server:port?security=reality&sni=...&type=ws&...`
- **VMess**：`vmess://base64encoded...`
- **Trojan**：`trojan://password@server:port?sni=...&type=ws&...`
- **tuic**：`tuic://uuid:password@server:port...`
- **anytls**：`anytls://uuid@server:port...`
- **hysteria2**：`hysteria2://base64@server:port...`
- **SOCKS5**：`socks5://user:pass@server:port` 或 `socks://user:pass@server:port`
