import io
from pathlib import Path
from typing import AsyncGenerator, Optional, Union

import ollama
from matplotlib import pyplot as plt
from ollama import AsyncClient


class VisionModel:
    def __init__(self, model_name):
        self.model_name = model_name
        self.client = AsyncClient()

    @staticmethod
    def create_message(
            role: str,
            content: str,
            images: Optional[list[Union[io.BytesIO, plt.Figure, Path]]] = None
    ) -> ollama.Message:
        """Wrapper over ollama.Message to handle images."""
        if images is None:
            return ollama.Message(role=role, content=content)

        decoded_imgs = []
        for img in images:
            if isinstance(img, plt.Figure):
                buffer = io.BytesIO()
                img.savefig(buffer, format="png")
            elif isinstance(img, (str, Path)):
                with open(img, "rb") as f:
                    decoded_imgs.append(ollama.Image(value=f.read()))
                continue
            else:
                assert isinstance(img, io.BytesIO)
                buffer = img
            buffer.seek(0)
            decoded_imgs.append(
                ollama.Image(value=buffer.read())
            )
        return ollama.Message(role=role, content=content, images=decoded_imgs)

    async def chat(self, messages: list[ollama.Message]) -> AsyncGenerator[str, None]:
        resp = await self.client.chat(
            model=self.model_name,
            messages=messages,
            stream=True
        )
        async for part in resp:
            yield part["message"]["content"]
