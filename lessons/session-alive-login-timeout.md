# 登录超时 Bug 复盘 (2026-07-16)

## 现象
用户每次 GitHub 登录完成后,主页始终显示"登录超时"。

## 根因
`_session_alive()` 对没有设备指纹的 session 设了 24 小时硬性过期(`UNREGISTERED_TTL`),
但 `/auth/callback` 在复用已有 session 时只更新 `expires`,不更新 `login` 时间戳。

导致 session 创建 24 小时后,无论用户怎么重新登录,`now - login > 24h` 始终为 True,
`_session_alive()` 永远返回 False,`valid_user()` 永远返回 None。

## 修复
在所有复用 session 的回调路径中加一行:
```python
v["login"] = now
```
涉及三处:standalone `/auth/callback`、direct `/auth/callback`、`/api/login-start`。

## 排查过程中的弯路
1. 一开始以为是前端轮询时序问题(弹窗关闭太快),改了前端,无效
2. 然后以为是跨域 cookie 问题(博客 token cookie 读不到),重构了登录流程,无效
3. 改完代码忘了部署到服务器(文件在本地,服务器跑的还是老代码)
4. 最终通过查 `rtw_sessions.json` 发现 session 的 login 时间是 17 天前且从未更新,
   对照 `_session_alive()` 的 `UNREGISTERED_TTL` 逻辑才定位到真正原因

## 教训
- 先看数据再改代码。如果一开始就查 `rtw_sessions.json` 的内容,对照 `_session_alive()` 的逻辑,几分钟就能定位
- 本地改完代码要确认部署到服务器并重启进程,否则改了等于没改
- 复用 session 时,所有时间相关的字段都要考虑是否需要刷新
