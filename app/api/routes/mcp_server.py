"""Model Context Protocol (MCP) Financial Server (HKUDS/AI-Trader & OpenAlice).

Exposes the platform's quant valuation, boardroom personas, and trading execution
tools over official Model Context Protocol (MCP) JSON-RPC standard for Cursor,
Claude Code, Windsurf, or external autonomous agents.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.boardroom_service import BoardroomService
from app.services.multi_agent_service import MultiAgentService
from app.services.valuation_service import ValuationService

router = APIRouter(prefix="/api/mcp", tags=["model-context-protocol"])


class MCPToolSchema(BaseModel):
    name: str
    description: str
    inputSchema: Dict[str, Any]


class MCPRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Any] = 1
    method: str
    params: Optional[Dict[str, Any]] = None


class MCPRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Any] = 1
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None


AVAILABLE_MCP_TOOLS: List[MCPToolSchema] = [
    MCPToolSchema(
        name="get_valuation_dcf",
        description="Calculate 3-scenario DCF and Reverse DCF for any equity symbol.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol e.g. AAPL"},
                "current_price": {"type": "number", "description": "Current stock price"},
            },
            "required": ["symbol", "current_price"],
        },
    ),
    MCPToolSchema(
        name="convene_boardroom_debate",
        description="Run investment committee debate among Warren Buffett, Cathie Wood, Michael Burry, etc.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker symbol"},
                "price": {"type": "number", "description": "Current price"},
            },
            "required": ["symbol"],
        },
    ),
    MCPToolSchema(
        name="simulate_paper_order",
        description="Stage and simulate an order on the paper trading desk.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "action": {"type": "string", "enum": ["BUY", "SELL"]},
                "quantity": {"type": "number"},
            },
            "required": ["symbol", "action", "quantity"],
        },
    ),
]


@router.post("/rpc", response_model=MCPRpcResponse)
async def handle_mcp_rpc(request: MCPRpcRequest) -> MCPRpcResponse:
    """Handles MCP JSON-RPC 2.0 requests."""
    method = request.method
    params = request.params or {}

    if method == "initialize":
        return MCPRpcResponse(
            id=request.id,
            result={
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ai-finance-intelligence-mcp", "version": "2.5.0"},
            },
        )

    elif method in {"tools/list", "list_tools"}:
        return MCPRpcResponse(
            id=request.id,
            result={"tools": [t.model_dump() for t in AVAILABLE_MCP_TOOLS]},
        )

    elif method in {"tools/call", "call_tool"}:
        tool_name = params.get("name")
        args = params.get("arguments", {})

        if tool_name == "get_valuation_dcf":
            sym = args.get("symbol", "AAPL")
            p = float(args.get("current_price", 150.0))
            svc = ValuationService()
            report = svc.generate_full_report(symbol=sym, current_price=p)
            return MCPRpcResponse(id=request.id, result={"content": [{"type": "text", "text": str(report.final_valuation_verdict)}]})

        elif tool_name == "convene_boardroom_debate":
            sym = args.get("symbol", "AAPL")
            p = float(args.get("price", 150.0))
            b_svc = BoardroomService()
            summary = b_svc.convene_boardroom(symbol=sym, price=p, pe_ratio=24.0, debt_to_equity=1.1, fcf_yield_pct=4.5, revenue_growth_3y_pct=14.0)
            return MCPRpcResponse(id=request.id, result={"content": [{"type": "text", "text": summary.synthesized_thesis}]})

        elif tool_name == "simulate_paper_order":
            sym = args.get("symbol", "AAPL").upper()
            action = args.get("action", "BUY")
            qty = float(args.get("quantity", 10.0))
            return MCPRpcResponse(id=request.id, result={"content": [{"type": "text", "text": f"Paper order simulated: {action} {qty} {sym} @ market price."}]})

        return MCPRpcResponse(
            id=request.id,
            error={"code": -32601, "message": f"Unknown tool name: {tool_name}"},
        )

    return MCPRpcResponse(
        id=request.id,
        error={"code": -32601, "message": f"Method not found: {method}"},
    )
