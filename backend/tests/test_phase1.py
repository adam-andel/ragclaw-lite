import httpx, asyncio, json

async def test():
    async with httpx.AsyncClient() as c:
        # Health check
        r = await c.get("http://127.0.0.1:8000/api/health")
        print(f"health: {r.status_code} {r.json()}")

        token = await login(c)

        # Test SKILL CRUD
        skill_id = await test_skills(c, token)

        # Test MCP Server CRUD
        server_id = await test_mcp_servers(c, token)

        # Test Skill-Tool binding
        if skill_id and server_id:
            await test_bind_tool(c, token, skill_id, server_id)

        print("\n=== Phase 1 全部 API 测试通过 ===")

async def login(c):
    r = await c.post("http://127.0.0.1:8000/api/auth/login", json={
        "username": "admin", "password": "admin123"
    })
    data = r.json()
    token = data["access_token"]
    print(f"login: OK (admin)")
    return token

async def test_skills(c, token):
    headers = {"Authorization": f"Bearer {token}"}
    base = "http://127.0.0.1:8000/api/skills"

    # Create
    r = await c.post(base, json={
        "name": "IT运维助手",
        "description": "专业的IT运维技能，帮用户解决服务器、网络、Docker等问题",
        "system_prompt": "你是一位资深IT运维工程师。根据知识库文档提供专业建议。",
    }, headers=headers)
    assert r.status_code == 201, f"Create skill failed: {r.status_code} {r.text}"
    skill = r.json()
    print(f"skill create: {skill['id'][:8]}... name={skill['name']}")

    # Update
    r = await c.patch(f"{base}/{skill['id']}", json={"description": "更新后的描述"}, headers=headers)
    assert r.status_code == 200
    print(f"skill update: OK")

    # List
    r = await c.get(base, headers=headers)
    data = r.json()
    assert data["total"] >= 1
    print(f"skill list: total={data['total']}")

    # Get
    r = await c.get(f"{base}/{skill['id']}", headers=headers)
    assert r.status_code == 200
    print(f"skill get: OK")

    return skill["id"]


async def test_mcp_servers(c, token):
    headers = {"Authorization": f"Bearer {token}"}
    base = "http://127.0.0.1:8000/api/mcp/servers"

    # Create
    r = await c.post(base, json={
        "name": "天气查询",
        "transport_type": "http",
        "endpoint": "https://weather-mcp.example.com/mcp",
    }, headers=headers)
    assert r.status_code == 201, f"Create server failed: {r.status_code} {r.text}"
    server = r.json()
    print(f"mcp server create: {server['id'][:8]}... name={server['name']}")

    # Update
    r = await c.patch(f"{base}/{server['id']}", json={"endpoint": "https://weather-v2.example.com/mcp"}, headers=headers)
    assert r.status_code == 200
    print(f"mcp server update: OK")

    # List
    r = await c.get(base, headers=headers)
    data = r.json()
    assert data["total"] >= 1
    print(f"mcp server list: total={data['total']}")

    # Get
    r = await c.get(f"{base}/{server['id']}", headers=headers)
    assert r.status_code == 200
    print(f"mcp server get: OK")

    # Test (will fail because endpoint is fake, but should not crash)
    r = await c.post(f"{base}/{server['id']}/test", headers=headers)
    result = r.json()
    print(f"mcp server test: ok={result.get('ok')} (expected: False, fake endpoint)")

    return server["id"]


async def test_bind_tool(c, token, skill_id, server_id):
    headers = {"Authorization": f"Bearer {token}"}

    # Bind
    r = await c.post(f"http://127.0.0.1:8000/api/skills/{skill_id}/tools", json={
        "tool_name": "get_weather",
        "mcp_server_id": server_id,
    }, headers=headers)
    assert r.status_code == 201, f"Bind tool failed: {r.status_code} {r.text}"
    bind = r.json()
    tool_id = bind["id"]
    print(f"tool bind: {tool_id[:8]}... tool={bind['tool_name']}")

    # Duplicate bind should fail
    r = await c.post(f"http://127.0.0.1:8000/api/skills/{skill_id}/tools", json={
        "tool_name": "get_weather",
        "mcp_server_id": server_id,
    }, headers=headers)
    assert r.status_code == 400
    print(f"tool bind duplicate: 400 (expected)")

    # Verify skill response includes tools
    r = await c.get(f"http://127.0.0.1:8000/api/skills/{skill_id}", headers=headers)
    skill = r.json()
    assert len(skill["tools"]) >= 1
    print(f"skill tools included: count={len(skill['tools'])}")

    # Unbind
    r = await c.delete(f"http://127.0.0.1:8000/api/skills/{skill_id}/tools/{tool_id}", headers=headers)
    assert r.status_code == 200
    print(f"tool unbind: OK")


asyncio.run(test())
