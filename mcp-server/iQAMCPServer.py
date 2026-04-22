from mcp.server.fastmcp import FastMCP
from natsClient import NATSClient
import asyncio


mcp_server = FastMCP(name="iQAMCPServer")
nc = NATSClient()

@mcp_server.tool(name="generate_uuid")
def generate_uuid():
    """This method is using to generate random UUIDv6 for test_id"""
    import uuid
    return str(uuid.uuid4())

@mcp_server.tool(name="start_test")
def start_test(test_id:str, sequence:int):
    """Start whole testing process with given id"""
    print(f"{sequence}.Test {test_id} started")
    asyncio.create_task(nc.publish("test.topic",{
        "event": "test_started",
        "test_id": test_id
    }))


@mcp_server.tool(name="end_test")
def end_test(test_id:str, sequence:int):
    """End testing process with given id"""
    print(f"{sequence}.Test {test_id} ended")
    asyncio.create_task(nc.publish("test.topic",{
        "event": "test_ended",
        "test_id": test_id
    }))

@mcp_server.tool(name="initiate_call")
def initiate_call(sequence:int,test_id:str, calling_party: str, called_party: str, duration: int):
    """This method is using to make phone call from given calling party to called party with given duration in seconds.
    Delay is in seconds is define after how many seconds function should be executed"""
    print(f"{sequence}.Test {test_id}: {calling_party} called {called_party} with duration {duration}")
    asyncio.create_task(nc.publish("test.topic",{"event": "call_initiated",
               "data":{
        "event": "call_initiated",
        "test_id": test_id,
        "calling_party": calling_party,
        "called_party": called_party,
        "duration": duration
    }
    }))

@mcp_server.tool(name="send_ussd")
def send_ussd(sequence:int,test_id:str, calling_party: str, ussd_code: str):
    """This method is using to send USSD request from given calling party to USSD code.
    Delay is in seconds is define after how many seconds function should be executed"""
    print(f"{sequence}.Test {test_id}: {calling_party} called {ussd_code} ")
    asyncio.create_task(nc.publish("test.topic",{"event": "ussd_sent",
               "data":{
        "event": "ussd_sent",
        "test_id": test_id,
        "calling_party": calling_party,
        "ussd_code": ussd_code
    }
    }))

@mcp_server.tool(name="send_sms")
def send_sms(sequence:int,test_id:str, calling_party: str, sms_code: str, text:str):
    """This method is using to send SMS request from given calling party to SMS code."""
    print(f"{sequence}.Test {test_id}: SMS sent {calling_party}  {sms_code} text {text}")
    asyncio.create_task(nc.publish("test.topic",{"event": "sms_sent",
               "data":{
        "event": "sms_sent",
        "test_id": test_id,
        "calling_party": calling_party,
        "sms_code": sms_code,
        "text": text
    }
    }))

@mcp_server.tool(name="wait")
def wait(sequence:int,test_id:str, delay: int):
    """This method is using to wait for a given delay in seconds."""
    print(f"{sequence}.Test {test_id}: Wait for {delay} seconds")
    asyncio.create_task(nc.publish("test.topic",{"event": "wait",
               "data":{
        "event": "wait",
        "test_id": test_id,
        "delay": delay
    }
    }))

@mcp_server.tool(name="unknown_tool")
def unknown_tool(sequence:int,test_id:str, tool_name: str):
    """This method is using to handle unknown tool calls."""
    print(f"{sequence}.Test {test_id}: Unknown tool called {tool_name}")
    asyncio.create_task(nc.publish("test.topic",{"event": "unknown_tool",
               "data":{
        "event": "unknown_tool",
        "test_id": test_id,
        "tool_name": tool_name
    }
    }))

async def main():
    await nc.connect()
    await mcp_server.run_streamable_http_async()

if __name__ == "__main__":
    asyncio.run(main())