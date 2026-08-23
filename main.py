import asyncio
import math
import os
import time
from threading import Thread
import aiohttp
import discord
from discord.ext import commands
from flask import Flask
import motor.motor_asyncio

# --- 1. SERVIDOR WEB (FLASK) ---
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

# --- 2. CONFIGURAÇÃO DO MONGODB ---
MONGO_URI = os.environ.get("MONGO_URI")
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client["blitz_tracker"]
snapshots_col = db["snapshots"]
tank_snapshots_col = db["tank_snapshots"]

async def init_db():
    # Cria índices para buscas super rápidas no MongoDB
    await snapshots_col.create_index([("account_id", 1), ("timestamp", -1)])
    await tank_snapshots_col.create_index([("account_id", 1), ("tank_id", 1), ("timestamp", -1)])

async def save_snapshot(account_id: int, battles: int, wins: int, damage: int):
    now = int(time.time())
    last = await snapshots_col.find_one({"account_id": account_id}, sort=[("timestamp", -1)])
    
    if not last or last.get("battles") != battles:
        await snapshots_col.insert_one({
            "account_id": account_id,
            "timestamp": now,
            "battles": battles,
            "wins": wins,
            "damage": damage
        })

async def get_delta(account_id: int, days: int, curr_b: int, curr_w: int, curr_d: int):
    target_ts = int(time.time()) - (days * 86400)
    old_snap = await snapshots_col.find_one(
        {"account_id": account_id, "timestamp": {"$lte": target_ts}},
        sort=[("timestamp", -1)]
    )
    
    if not old_snap:
        old_snap = await snapshots_col.find_one({"account_id": account_id}, sort=[("timestamp", 1)])
        
    if old_snap and curr_b > old_snap.get("battles", 0):
        b_delta = curr_b - old_snap["battles"]
        w_delta = curr_w - old_snap["wins"]
        d_delta = curr_d - old_snap["damage"]
        return b_delta, w_delta, d_delta
    return 0, 0, 0

async def save_tank_snapshot(account_id: int, tank_id: int, battles: int, wins: int, damage: int):
    now = int(time.time())
    last = await tank_snapshots_col.find_one({"account_id": account_id, "tank_id": tank_id}, sort=[("timestamp", -1)])
    
    if not last or last.get("battles") != battles:
        await tank_snapshots_col.insert_one({
            "account_id": account_id,
            "tank_id": tank_id,
            "timestamp": now,
            "battles": battles,
            "wins": wins,
            "damage": damage
        })

async def get_tank_delta(account_id: int, tank_id: int, days: int, curr_b: int, curr_w: int, curr_d: int):
    target_ts = int(time.time()) - (days * 86400)
    old_snap = await tank_snapshots_col.find_one(
        {"account_id": account_id, "tank_id": tank_id, "timestamp": {"$lte": target_ts}},
        sort=[("timestamp", -1)]
    )
    
    if not old_snap:
        old_snap = await tank_snapshots_col.find_one({"account_id": account_id, "tank_id": tank_id}, sort=[("timestamp", 1)])
        
    if old_snap and curr_b > old_snap.get("battles", 0):
        b_delta = curr_b - old_snap["battles"]
        w_delta = curr_w - old_snap["wins"]
        d_delta = curr_d - old_snap["damage"]
        return b_delta, w_delta, d_delta
    return 0, 0, 0

# NOVO: Função que registra TODOS os tanques do jogador de uma vez
async def sync_all_tanks(account_id: int, base_url: str, session: aiohttp.ClientSession):
    tanks_url = f"{base_url}/wotb/tanks/stats/?application_id={APPLICATION_ID}&account_id={account_id}"
    try:
        async with session.get(tanks_url, timeout=12) as resp:
            if resp.status == 200:
                data = await resp.json()
                user_tanks = data.get("data", {}).get(str(account_id), []) or []
                for u_tank in user_tanks:
                    t_id = u_tank.get("tank_id")
                    stats = u_tank.get("all", {})
                    await save_tank_snapshot(account_id, t_id, stats.get("battles", 0), stats.get("wins", 0), stats.get("damage_dealt", 0))
    except Exception as e:
        print(f"Erro ao sincronizar todos os tanques: {e}")

# --- 3. CONFIGURAÇÃO DO BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

async def find_player(nickname: str, session: aiohttp.ClientSession):
    regions = {
        "na": "https://api.wotblitz.com",
        "eu": "https://api.wotblitz.eu",
        "asia": "https://api.wotblitz.asia",
    }
    timeout = aiohttp.ClientTimeout(total=10)
    for reg_code, base_url in regions.items():
        search_url = f"{base_url}/wotb/account/list/?application_id={APPLICATION_ID}&search={nickname}"
        try:
            async with session.get(search_url, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "ok" and data.get("data"):
                        return reg_code, base_url, data["data"][0]["account_id"], data["data"][0]["nickname"]
            await asyncio.sleep(0.1)
        except Exception:
            pass
    return None, None, None, None

async def get_tank_encyclopedia(base_url: str, session: aiohttp.ClientSession):
    try:
        async with session.get("https://www.blitzstars.com/api/tanks", timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                tanks_dict = {}
                for tank in data:
                    t_id = tank.get("tank_id")
                    if t_id:
                        tanks_dict[int(t_id)] = {
                            "name": tank.get("name"),
                            "tier": tank.get("tier"),
                            "type": tank.get("type"),
                            "images": {"preview_image": f"https://glossary-wotblitz.gvt.wargaming.net/icons/pay_icon_{t_id}.png"}
                        }
                return tanks_dict
    except Exception:
        pass
    url = f"{base_url}/wotb/encyclopedia/vehicles/?application_id={APPLICATION_ID}&fields=tank_id,name,tier,type,images"
    try:
        async with session.get(url, timeout=12) as resp:
            if resp.status == 200:
                data = await resp.json()
                return {int(k): v for k, v in data.get("data", {}).items()}
    except Exception:
        pass
    return {}

@bot.event
async def on_ready():
    await init_db()
    print(f"Bot conectado com sucesso como {bot.user}")

@bot.command()
async def blitz(ctx):
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        await ctx.send(
            "🎮 **Menu Blitz**\n"
            "O que você deseja fazer?\n"
            "1️⃣ Estatísticas Gerais do Jogador\n"
            "2️⃣ Estatísticas de um Tanque Específico 🛡️\n"
            "3️⃣ Registro de Todos os Tanques Jogados no Período 📋\n"
            "4️⃣ Calcular Meta de Winrate 🧮\n\n"
            "*Digite `1`, `2`, `3` ou `4`:*"
        )
        msg_opcao = await bot.wait_for("message", check=check, timeout=90.0)
        opcao = msg_opcao.content.strip()

        if opcao == "1":
            await ctx.send("Qual é o **nickname** do jogador?")
            msg_nick = await bot.wait_for("message", check=check, timeout=90.0)
            nickname = msg_nick.content.strip()

            await ctx.send(
                "Escolha o **período**:\n1️⃣ 24 Horas (`1d`)\n2️⃣ 7 Dias (`7d`)\n3️⃣ 30 Dias (`30d`)\n\n*Digite `1`, `2` ou `3`:*"
            )
            msg_periodo = await bot.wait_for("message", check=check, timeout=90.0)
            escolha = msg_periodo.content.strip().lower()

            days_map = {"1": (1, "24 Horas"), "1d": (1, "24 Horas"), "2": (7, "7 Dias"), "7d": (7, "7 Dias"), "3": (30, "30 Dias"), "30d": (30, "30 Dias")}
            days_limit, period_label = days_map.get(escolha, (30, "30 Dias"))

            loading_msg = await ctx.send(f"🔍 Consultando dados e sincronizando garagem de **{nickname}**...")

            async with aiohttp.ClientSession() as session:
                region_code, base_url, account_id, player_name = await find_player(nickname, session)
                if not account_id:
                    await loading_msg.edit(content=f"❌ Jogador **{nickname}** não encontrado.")
                    return

                # Sincroniza garagem inteira em segundo plano
                bot.loop.create_task(sync_all_tanks(account_id, base_url, session))

                info_url = f"{base_url}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={account_id}"
                async with session.get(info_url, timeout=12) as resp:
                    wg_data = await resp.json()
                    stats_all = wg_data.get("data", {}).get(str(account_id), {}).get("statistics", {}).get("all", {})

                curr_battles = stats_all.get("battles", 0)
                curr_wins = stats_all.get("wins", 0)
                curr_damage = stats_all.get("damage_dealt", 0)

                b_delta, w_delta, d_delta = await get_delta(account_id, days_limit, curr_battles, curr_wins, curr_damage)
                await save_snapshot(account_id, curr_battles, curr_wins, curr_damage)

                if b_delta > 0:
                    wr_delta = (w_delta / b_delta) * 100
                    avg_dmg_delta = d_delta / b_delta
                    periodo_txt = f"• **Batalhas:** {b_delta:,}\n• **Vitórias:** {w_delta:,}V / {b_delta - w_delta:,}D\n• **WR Recente:** {wr_delta:.2f}%\n• **Dano Médio Recente:** {avg_dmg_delta:.0f}"
                else:
                    periodo_txt = f"Registro atualizado para **{player_name}** e toda a garagem!\n*Jogue novas partidas e consulte novamente.*"

                wr_all = (curr_wins / curr_battles * 100) if curr_battles > 0 else 0
                avg_dmg_all = (curr_damage / curr_battles) if curr_battles > 0 else 0
                geral_txt = f"• **Total de Batalhas:** {curr_battles:,}\n• **WR Geral:** {wr_all:.2f}%\n• **Dano Médio Geral:** {avg_dmg_all:.0f}"

                embed = discord.Embed(title=f"Estatísticas de {player_name} [{region_code.upper()}]", color=0x3498DB)
                embed.add_field(name=f"📊 Desempenho Recente ({period_label})", value=periodo_txt, inline=False)
                embed.add_field(name="🏆 Carreira (Geral)", value=geral_txt, inline=False)
                await loading_msg.edit(content="", embed=embed)

        elif opcao == "2":
            await ctx.send("Qual é o **nickname** do jogador?")
            msg_nick = await bot.wait_for("message", check=check, timeout=90.0)
            nickname = msg_nick.content.strip()

            await ctx.send("Qual é o **nome do tanque**?")
            msg_tank = await bot.wait_for("message", check=check, timeout=90.0)
            search_tank_name = msg_tank.content.strip().lower()

            await ctx.send("Qual **período**?\n1️⃣ **Carreira**\n2️⃣ **24 Horas**\n3️⃣ **Última Semana**\n4️⃣ **Último Mês**\n\n*Digite `1`, `2`, `3` ou `4`:*")
            msg_modo = await bot.wait_for("message", check=check, timeout=90.0)
            modo_input = msg_modo.content.strip().lower()

            days_map = {"1": (0, "Carreira"), "2": (1, "Últimas 24 Horas"), "3": (7, "Última Semana"), "4": (30, "Último Mês")}
            days_limit, period_label = days_map.get(modo_input, (0, "Carreira"))

            loading_msg = await ctx.send(f"🔍 Buscando **{msg_tank.content}**...")

            async with aiohttp.ClientSession() as session:
                region_code, base_url, account_id, player_name = await find_player(nickname, session)
                if not account_id:
                    await loading_msg.edit(content=f"❌ Jogador **{nickname}** não encontrado.")
                    return

                encyclopedia = await get_tank_encyclopedia(base_url, session)
                clean_search = search_tank_name.replace("-", "").replace(" ", "").replace("_", "")
                matching_tanks = {int(t_id): info for t_id, info in encyclopedia.items() if clean_search in info.get("name", "").lower().replace("-", "").replace(" ", "").replace("_", "")}

                tanks_url = f"{base_url}/wotb/tanks/stats/?application_id={APPLICATION_ID}&account_id={account_id}"
                async with session.get(tanks_url, timeout=12) as resp:
                    user_tanks = (await resp.json()).get("data", {}).get(str(account_id), []) or []

                found_stats = []
                for u_tank in user_tanks:
                    tank_id = u_tank.get("tank_id")
                    if tank_id in matching_tanks:
                        stats = u_tank.get("all", {})
                        found_stats.append({
                            "tank_id": tank_id, "name": matching_tanks[tank_id].get("name"),
                            "tier": matching_tanks[tank_id].get("tier"), "type": matching_tanks[tank_id].get("type"),
                            "icon": matching_tanks[tank_id].get("images", {}).get("preview_image"),
                            "battles": stats.get("battles", 0), "wins": stats.get("wins", 0),
                            "damage": stats.get("damage_dealt", 0), "shots": stats.get("shots", 0),
                            "hits": stats.get("hits", 0), "frags": stats.get("frags", 0),
                        })

                if not found_stats:
                    await loading_msg.edit(content=f"❌ **{player_name}** nunca jogou com o tanque **{msg_tank.content}**.")
                    return

                selected_tank = max(found_stats, key=lambda x: x["battles"])
                t_id = selected_tank["tank_id"]
                b_curr, w_curr, d_curr = selected_tank["battles"], selected_tank["wins"], selected_tank["damage"]

                embed = discord.Embed(title=f"{selected_tank['name']} (Tier {selected_tank['tier']})", color=0xE67E22)
                
                if days_limit > 0:
                    b_delta, w_delta, d_delta = await get_tank_delta(account_id, t_id, days_limit, b_curr, w_curr, d_curr)
                    await save_tank_snapshot(account_id, t_id, b_curr, w_curr, d_curr)

                    if b_delta > 0:
                        embed.add_field(name=f"📊 Desempenho ({period_label})", value=f"• **Batalhas:** {b_delta:,}\n• **Vitórias:** {w_delta:,}V / {b_delta-w_delta:,}D\n• **Winrate:** {(w_delta/b_delta)*100:.2f}%\n• **Dano Médio:** {d_delta/b_delta:.0f}")
                    else:
                        embed.add_field(name=f"📊 Desempenho ({period_label})", value="Nenhuma nova partida registrada com este tanque no período.")
                else:
                    await save_tank_snapshot(account_id, t_id, b_curr, w_curr, d_curr)
                    embed.add_field(name="📊 Carreira", value=f"• **Batalhas:** {b_curr:,}\n• **Winrate:** {(w_curr/b_curr*100) if b_curr else 0:.2f}%\n• **Dano Médio:** {(d_curr/b_curr) if b_curr else 0:.0f}")

                if selected_tank["icon"]:
                    embed.set_thumbnail(url=selected_tank["icon"])
                await loading_msg.edit(content="", embed=embed)

        elif opcao == "3":
            await ctx.send("Qual é o **nickname** do jogador?")
            msg_nick = await bot.wait_for("message", check=check, timeout=90.0)
            nickname = msg_nick.content.strip()

            await ctx.send("Escolha o **período**:\n1️⃣ **24 Horas**\n2️⃣ **7 Dias**\n3️⃣ **30 Dias**\n\n*Digite `1`, `2` ou `3`:*")
            msg_periodo = await bot.wait_for("message", check=check, timeout=90.0)
            days_limit, period_label = {"1": (1, "24 Horas"), "2": (7, "7 Dias"), "3": (30, "30 Dias")}.get(msg_periodo.content.strip(), (1, "24 Horas"))

            loading_msg = await ctx.send(f"📋 Compilando tanques para **{nickname}**...")

            async with aiohttp.ClientSession() as session:
                region_code, base_url, account_id, player_name = await find_player(nickname, session)
                if not account_id:
                    await loading_msg.edit(content=f"❌ Jogador não encontrado.")
                    return

                encyclopedia = await get_tank_encyclopedia(base_url, session)
                async with session.get(f"{base_url}/wotb/tanks/stats/?application_id={APPLICATION_ID}&account_id={account_id}", timeout=12) as resp:
                    user_tanks = (await resp.json()).get("data", {}).get(str(account_id), []) or []

                played_tanks_delta = []
                for u_tank in user_tanks:
                    t_id = u_tank.get("tank_id")
                    stats = u_tank.get("all", {})
                    b_curr, w_curr, d_curr = stats.get("battles", 0), stats.get("wins", 0), stats.get("damage_dealt", 0)

                    b_delta, w_delta, d_delta = await get_tank_delta(account_id, t_id, days_limit, b_curr, w_curr, d_curr)
                    await save_tank_snapshot(account_id, t_id, b_curr, w_curr, d_curr)

                    if b_delta > 0:
                        tank_info = encyclopedia.get(t_id, {})
                        played_tanks_delta.append({
                            "name": tank_info.get("name", f"Tanque #{t_id}"), "tier": tank_info.get("tier", "?"),
                            "battles": b_delta, "wins": w_delta, "damage": d_delta
                        })

                if not played_tanks_delta:
                    await loading_msg.edit(content=f"📋 **{player_name}**: Nenhum registro no período. Snapshots gravados!")
                    return

                played_tanks_delta.sort(key=lambda x: x["battles"], reverse=True)
                lines = [f"• **{t['name']}** (T{t['tier']}): {t['battles']}B | WR: **{(t['wins']/t['battles'])*100:.1f}%** | Dmg: **{t['damage']/t['battles']:.0f}**" for t in played_tanks_delta[:15]]

                embed = discord.Embed(title=f"📋 Tanques Jogados — {player_name} ({period_label})", description="\n".join(lines), color=0x9B59B6)
                await loading_msg.edit(content="", embed=embed)

        elif opcao == "4":
            await ctx.send("🧮 **Calculadora de Meta de Winrate (Em Construção)**")

    except asyncio.TimeoutError:
        await ctx.send("⏰ **Tempo esgotado!** Digite `!blitz` para tentar novamente.")

# --- 4. INICIALIZAÇÃO ---
keep_alive()
bot.run(DISCORD_TOKEN)
