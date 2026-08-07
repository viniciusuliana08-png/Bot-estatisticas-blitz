import asyncio
import os
from threading import Thread
import aiohttp
import discord
from discord.ext import commands
from flask import Flask

# --- 1. SERVIDOR WEB PARA MANTER NO RENDER ---
app = Flask("")


@app.route("/")
def home():
  return "Bot online!"


def run():
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)


def keep_alive():
  t = Thread(target=run)
  t.start()


# --- 2. CONFIGURAÇÃO DO BOT DO DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")


@bot.event
async def on_ready():
  print(f"Bot online com sucesso como {bot.user}")


@bot.command()
async def blitz(ctx, *, nickname: str):
  msg = await ctx.send(f"Procurando por **{nickname}** nos servidores...")

  try:
    async with aiohttp.ClientSession() as session:
      # Teste de diagnóstico na API do servidor NA
      url = f"https://api.wotblitz.na/wotb/account/list/?application_id={APPLICATION_ID}&search={nickname}"

      async with session.get(
          url, timeout=aiohttp.ClientTimeout(total=10)
      ) as resp:
        data = await resp.json()

        # Mostra o status HTTP e o retorno bruto da Wargaming no Discord
        await msg.edit(
            content=(
                f"**Status HTTP:** `{resp.status}`\n"
                f"**APP_ID usado:** `{APPLICATION_ID}`\n"
                f"**Resposta da API:**\n```json\n{data}\n```"
            )
        )

  except Exception as e:
    await msg.edit(content=f"Erro de conexão ao tentar buscar: `{e}`")


# --- 3. INICIALIZAÇÃO ---
keep_alive()
bot.run(DISCORD_TOKEN)
