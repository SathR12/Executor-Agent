"""Expose read-only sample sales data as MCP tools."""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("Sales Server")


@mcp.tool()
def get_orders(status: str) -> list[dict]:
    """Return sample orders whose status exactly matches the supplied value.

    Args:
        status: One of "pending", "completed", or "out for delivery". For example,
            use "completed", not "complete". Matching is case-sensitive.

    Returns:
        A list of orders with id, amount, and status fields, or an empty list if
        nothing matches. Amounts are sample dollar values.
    """
    orders = [
        {"id": 1, "amount": 750.00, "status": "pending"},
        {"id": 2, "amount": 250.00, "status": "completed"},
        {"id": 3, "amount": 69.99, "status": "out for delivery"},
        {"id": 4, "amount": 1250.00, "status": "pending"},
        {"id": 5, "amount": 999.99, "status": "pending"},
    ]

    # Filter by status
    result = []
    for order in orders:
        if order["status"] == status:
            result.append(order)

    return result


@mcp.tool()
def get_customers(customer_id: int) -> dict:
    """Look up one sample customer by ID; this tool does not list or search customers.

    Args:
        customer_id: Numeric ID: 1 is Alex in New York, 2 is Sarah in New Jersey,
            and 3 is Vincent in Costa Mesa.

    Returns:
        A dictionary with name and city, or an empty dictionary for an unknown ID.
    """
    customers = {
        1: {"name": "Alex", "city": "New York"},
        2: {"name": "Sarah", "city": "New Jersey"},
        3: {"name": "Vincent", "city": "Costa Mesa"},
    }

    return customers.get(customer_id, {})


if __name__ == "__main__":
    mcp.run(transport="stdio")
