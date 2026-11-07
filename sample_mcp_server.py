"""Optional read-only MCP fixture, for stdio or Streamable HTTP integration tests."""
import argparse
from pydantic import BaseModel
class Profile(BaseModel):
 entity_id:str
 is_mock:bool
 source_ref:str
 document_date:str
 excerpt:str
 risk:str
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
mcp=FastMCP('Member Service Local Fixture',host='127.0.0.1',port=8878,json_response=True)
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
def get_demo_profile(entity_id:str)->Profile:
 """Return explicitly simulated enterprise evidence; not a real business data source."""
 if entity_id not in ('企业 A','企业 B','企业 C'):raise ValueError('示例仅支持企业A/B/C')
 return Profile(entity_id=entity_id,is_mock=True,source_ref='SIM-MCP-001',document_date='2026-10-31',excerpt='模拟资料：本地MCP联调返回，仅供演示。',risk='已核验失信' if entity_id=='企业 B' else '待核实')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--transport',choices=['stdio','streamable-http'],default='stdio');a=p.parse_args();mcp.run(transport=a.transport)
