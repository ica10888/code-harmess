"""插件 deploy_game：和默认工作流 frontend-game-deploy 同名。三个阶段：拷贝文件 → 启动并判断成败 → 返回访问地址。

payload 是触发 eureka 的那份 JSON（已通过工作流的 schema）：{"entry", "port"(32000-33000), "startCommand", "url", "desc"}。
"""
