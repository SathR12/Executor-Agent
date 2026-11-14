from mcp.server.mcpserver import MCPServer
mcp = MCPServer("Sales Server")

@mcp.tool()
def get_orders(status: "str") -> list[dict]:
    orders = [
        {"id": 1, "amount": 750.00, "status": "pending"},
        {"id": 2, "amount": 250.00, "status": "completed"},
        {"id": 3, "amount": 69.99, "status": "out for delivery"},
        {"id": 4, "amount": 1250.00, "status": "pending"},
        {"id": 5, "amount": 999.99, "status": "pending"}
    ]

    # Filter by status
    result = []
    for order in orders:
        if order["status"] == status:
            result.append(order)


    return result

@mcp.tool()
def get_customers(customer_id: int) -> dict: 
    customers = {
        1: {"name": "Alex", "city": "New York"},
        2: {"name": "Sarah", "city": "New Jersey"},
        3: {"name": "Vincent", "city": "Costa Mesa"}
    }

    return customers.get(customer_id, {})

if __name__ == "__main__":
    mcp.run(transport="stdio")








