import nats
from nats.errors import TimeoutError
import asyncio
import json

global nc

class NATSClient:
    def __init__(self):
        self.nc = None

    async def connect(self):
        self.nc =  await nats.connect("nats://localhost:4222")
        print("Connected to NATS server")
    
    async def publish(self, subject, message):
        if self.nc is None:
            raise Exception("NATS client is not connected")
        await self.nc.publish(subject, json.dumps(message).encode())
        print(f"Message published to {subject}")
