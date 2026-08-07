import os
import asyncio
from threading import Thread
import aiohttp
import discord
from discord.ext import commands
from flask import Flask

# --- CONFIGURAÇÃO WEB ---
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

# --- CONFIGURAÇÃO DO BOT ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

async def find_player(nickname: str, session: aiohttp.ClientSession):
    regions = ["na", "eu", "asia"]
    timeout = aiohttp.ClientTimeout(total=10) # Timeout de 10 segundos
    
    for region in regions:
        search_url = f"https://api.wotblitz.{region}/wotb/account/list/?application_id={APPLICATION_ID}&search={nickname}"
        try:
            async with session.get(search_url, timeout=timeout) as resp:
                print(f"DEBUG: Tentando região {region}, status: {resp.status}")
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("data") and len(data["data"]) > 0:
                        return region, data["data"][0]["account_id"], data["data"][0]["nickname"]
        except Exception as e:
            print(f"ERRO ao buscar na região {region}: {e}")
    return None, None, None

@bot.event
async def on_ready():
    print(f"Bot online como {bot.user}")

@bot.command()
async def blitz(ctx, *, nickname: str):
    msg = await ctx.send(f"Procurando por **{nickname}** nos servidores...")
    
    try:
        async with aiohttp.ClientSession() as session:
            region, account_id, player_name = await find_player(nickname, session)

            if not account_id:
                await msg.edit(content=f"Jogador **{nickname}** não encontrado ou erro na API.")
                return

            # Busca estatísticas
            blitzstars_url = f"https://www.blitzstars.com/api/playerstats/{account_id}"
            async with session.get(blitzstars_url, timeout=10) as resp:
                bs_data = await resp.json() if resp.status == 200 else {}

            info_url = f"https://api.wotblitz.{region}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={account_id}"
            async with session.get(info_url, timeout=10) as resp:
                wg_data = await resp.json()
                stats_all = wg_data["data"][str(account_id)]["statistics"]["all"]

            # (O resto do seu código de formatação continua igual)
            battles_all = stats_all["battles"]
            wins_all = stats_all["wins"]
            wr_all = (wins_all / battles_all * 100) if battles_all > 0 else 0
            
            geral_txt = f"**Total de Batalhas:** {battles_all:,}\n**WR Geral:** {wr_all:.2f}%"

            embed = discord.Embed(title=f"Estatísticas de {player_name}", color=discord.Color.gold())
            embed.add_field(name="Geral", value=geral_txt, inline=False)
            await msg.edit(content=None, embed=embed)

    except Exception as e:
        print(f"ERRO CRÍTICO NO COMANDO BLITZ: {e}")
        await msg.edit(content=f"Ocorreu um erro ao buscar dados: {str(e)}")

keep_alive()
bot.run(DISCORD_TOKEN)
