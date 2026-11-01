import json
import httpx
from backend.orchestrator.providers import ChatProvider, LAST_USAGE
from backend.orchestrator.models import Decomposition


async def test_rate_limit_retry_and_json_repair_preserve_usage(monkeypatch):
    calls=[]
    def handle(request):
        calls.append(json.loads(request.content))
        if len(calls)==1:
            return httpx.Response(429,headers={'Retry-After':'0'})
        content='not json' if len(calls)==2 else '{"tasks":[]}'
        return httpx.Response(200,json={'choices':[{'message':{'content':content}}],'usage':{'total_tokens':7}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handle),**kw))
    async def no_wait(delay): pass
    monkeypatch.setattr('backend.orchestrator.providers.asyncio.sleep',no_wait)
    result=await ChatProvider('http://mock/v1','test').generate('test',{},Decomposition)
    assert result.tasks==[] and len(calls)==3
    assert LAST_USAGE.get()['total_tokens']==14
    assert 'previous response' in calls[-1]['messages'][-1]['content']


async def test_authentication_errors_do_not_retry(monkeypatch):
    import pytest
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(401)
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(httpx.HTTPStatusError):
        await ChatProvider('http://mock/v1','test').generate('test',{},Decomposition)
    assert len(calls)==1
