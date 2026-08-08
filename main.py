import asyncio
import math
import os
import sqlite3
import time
from threading import Thread
import aiohttp
import discord
from discord.ext import commands
from flask import Flask

# --- 1. SERVIDOR WEB (FLASK PARA MANTER ON-LINE) ---
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


# --- 2. BANCO DE DADOS LOCAL DE DELTAS ---
DB_NAME = "blitz_tracker.db"


def init_db():
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()

  # Tabela para snapshots gerais da conta
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            account_id INTEGER,
            timestamp INTEGER,
            battles INTEGER,
            wins INTEGER,
            damage INTEGER,
            PRIMARY KEY (account_id, timestamp)
        )
    """)

  # Tabela para snapshots individuais por tanque
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS tank_snapshots (
            account_id INTEGER,
            tank_id INTEGER,
            timestamp INTEGER,
            battles INTEGER,
            wins INTEGER,
            damage INTEGER,
            PRIMARY KEY (account_id, tank_id, timestamp)
        )
    """)
  conn.commit()
  conn.close()


def save_snapshot(
    account_id: int, battles: int, wins: int, damage: int
) -> None:
  now = int(time.time())
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()

  cursor.execute(
      """
        SELECT battles FROM snapshots 
        WHERE account_id = ? 
        ORDER BY timestamp DESC LIMIT 1
    """,
      (account_id,),
  )
  last = cursor.fetchone()

  if last is None or last[0] != battles:
    cursor.execute(
        """
            INSERT INTO snapshots (account_id, timestamp, battles, wins, damage)
            VALUES (?, ?, ?, ?, ?)
        """,
        (account_id, now, battles, wins, damage),
    )
    conn.commit()
  conn.close()


def get_delta(
    account_id: int, days: int, curr_b: int, curr_w: int, curr_d: int
):
  target_ts = int(time.time()) - (days * 86400)
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()

  cursor.execute(
      """
        SELECT battles, wins, damage FROM snapshots 
        WHERE account_id = ? AND timestamp <= ? 
        ORDER BY timestamp DESC LIMIT 1
    """,
      (account_id, target_ts),
  )
  old_snap = cursor.fetchone()

  if not old_snap:
    cursor.execute(
        """
            SELECT battles, wins, damage FROM snapshots 
            WHERE account_id = ? 
            ORDER BY timestamp ASC LIMIT 1
        """,
        (account_id,),
    )
    old_snap = cursor.fetchone()

  conn.close()

  if old_snap and curr_b > old_snap[0]:
    b_delta = curr_b - old_snap[0]
    w_delta = curr_w - old_snap[1]
    d_delta = curr_d - old_snap[2]
    return b_delta, w_delta, d_delta

  return 0, 0, 0


def save_tank_snapshot(
    account_id: int, tank_id: int, battles: int, wins: int, damage: int
):
  now = int(time.time())
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()

  cursor.execute(
      """
        SELECT battles FROM tank_snapshots 
        WHERE account_id = ? AND tank_id = ? 
        ORDER BY timestamp DESC LIMIT 1
    """,
      (account_id, tank_id),
  )
  last = cursor.fetchone()

  if last is None or last[0] != battles:
    cursor.execute(
        """
            INSERT INTO tank_snapshots (account_id, tank_id, timestamp, battles, wins, damage)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
        (account_id, tank_id, now, battles, wins, damage),
    )
    conn.commit()
  conn.close()


def get_tank_delta(
    account_id: int,
    tank_id: int,
    days: int,
    curr_b: int,
    curr_w: int,
    curr_d: int,
):
  target_ts = int(time.time()) - (days * 86400)
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()

  cursor.execute(
      """
        SELECT battles, wins, damage FROM tank_snapshots 
        WHERE account_id = ? AND tank_id = ? AND timestamp <= ? 
        ORDER BY timestamp DESC LIMIT 1
    """,
      (account_id, tank_id, target_ts),
  )
  old_snap = cursor.fetchone()

  if not old_snap:
    cursor.execute(
        """
            SELECT battles, wins, damage FROM tank_snapshots 
            WHERE account_id = ? AND tank_id = ? 
            ORDER BY timestamp ASC LIMIT 1
        """,
        (account_id, tank_id),
    )
    old_snap = cursor.fetchone()

  conn.close()

  if old_snap and curr_b > old_snap[0]:
    b_delta = curr_b - old_snap[0]
    w_delta = curr_w - old_snap[1]
    d_delta = curr_d - old_snap[2]
    return b_delta, w_delta, d_delta

  return 0, 0, 0


init_db()

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
          if (
              data.get("status") == "ok"
              and data.get("data")
              and len(data["data"]) > 0
          ):
            return (
                reg_code,
                base_url,
                data["data"][0]["account_id"],
                data["data"][0]["nickname"],
            )
        await asyncio.sleep(0.1)
    except Exception as e:
      print(f"Erro na busca ({reg_code}): {e}")

  return None, None, None, None


async def get_tank_encyclopedia(base_url: str, session: aiohttp.ClientSession):
  try:
    async with session.get(
        "https://www.blitzstars.com/api/tanks",
        timeout=aiohttp.ClientTimeout(total=8),
    ) as resp:
      if resp.status == 200:
        data = await resp.json()
        tanks_dict = {}
        if isinstance(data, list):
          for tank in data:
            t_id = tank.get("tank_id")
            if t_id:
              tanks_dict[int(t_id)] = {
                  "name": tank.get("name"),
                  "tier": tank.get("tier"),
                  "type": tank.get("type"),
                  "images": {
                      "preview_image": (
                          f"https://glossary-wotblitz.gvt.wargaming.net/icons/pay_icon_{t_id}.png"
                      )
                  },
              }
          return tanks_dict
  except Exception as e:
    print(f"Erro ao buscar tanques via BlitzStars: {e}")

  url = f"{base_url}/wotb/encyclopedia/vehicles/?application_id={APPLICATION_ID}&fields=tank_id,name,tier,type,images"
  try:
    async with session.get(
        url, timeout=aiohttp.ClientTimeout(total=12)
    ) as resp:
      if resp.status == 200:
        data = await resp.json()
        raw_data = data.get("data", {})
        return {int(k): v for k, v in raw_data.items()}
  except Exception as e:
    print(f"Erro ao buscar tanques via WG: {e}")

  return {}


@bot.event
async def on_ready():
  print(f"Bot conectado com sucesso como {bot.user}")


@bot.command()
async def blitz(ctx):
  def check(m):
    return m.author == ctx.author and m.channel == ctx.channel

  try:
    await ctx.send(
        "🎮 **Menu Blitz**\n"
        "O que você deseja fazer?\n"
        "1️⃣ Ver Estatísticas Gerais de um Jogador\n"
        "2️⃣ Ver Estatísticas de um Tanque Específico 🛡️\n"
        "3️⃣ Calcular Meta de Winrate 🧮\n\n"
        "*Digite `1`, `2` ou `3`:*"
    )
    msg_opcao = await bot.wait_for("message", check=check, timeout=90.0)
    opcao = msg_opcao.content.strip()

    # --- OPÇÃO 1: ESTATÍSTICAS GERAIS ---
    if opcao == "1":
      await ctx.send("Qual é o **nickname** do jogador?")
      msg_nick = await bot.wait_for("message", check=check, timeout=90.0)
      nickname = msg_nick.content.strip()

      await ctx.send(
          "Escolha o **período**:\n"
          "1️⃣ 24 Horas (`1d`)\n"
          "2️⃣ 7 Dias (`7d`)\n"
          "3️⃣ 30 Dias (`30d`)\n"
          "4️⃣ 90 Dias (`90d`)\n\n"
          "*Digite `1`, `2`, `3` ou `4`:*"
      )
      msg_periodo = await bot.wait_for("message", check=check, timeout=90.0)
      escolha = msg_periodo.content.strip().lower()

      days_map = {
          "1": (1, "24 Horas"),
          "1d": (1, "24 Horas"),
          "2": (7, "7 Dias"),
          "7d": (7, "7 Dias"),
          "3": (30, "30 Dias"),
          "30d": (30, "30 Dias"),
          "4": (90, "90 Dias"),
          "90d": (90, "90 Dias"),
      }
      days_limit, period_label = days_map.get(escolha, (30, "30 Dias"))

      loading_msg = await ctx.send(
          f"🔍 Consultando dados oficiais de **{nickname}**..."
      )

      async with aiohttp.ClientSession() as session:
        region_code, base_url, account_id, player_name = await find_player(
            nickname, session
        )
        if not account_id:
          await loading_msg.edit(
              content=f"❌ Jogador **{nickname}** não foi encontrado."
          )
          return

        timeout = aiohttp.ClientTimeout(total=12)
        info_url = f"{base_url}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={account_id}"
        async with session.get(info_url, timeout=timeout) as resp:
          wg_data = await resp.json()
          player_info = wg_data.get("data", {}).get(str(account_id), {})
          stats_all = player_info.get("statistics", {}).get("all", {})

        curr_battles = stats_all.get("battles", 0)
        curr_wins = stats_all.get("wins", 0)
        curr_damage = stats_all.get("damage_dealt", 0)

        b_delta, w_delta, d_delta = get_delta(
            account_id, days_limit, curr_battles, curr_wins, curr_damage
        )
        save_snapshot(account_id, curr_battles, curr_wins, curr_damage)

        if b_delta > 0:
          l_delta = b_delta - w_delta
          wr_delta = (w_delta / b_delta) * 100
          avg_dmg_delta = d_delta / b_delta
          periodo_txt = (
              f"• **Batalhas:** {b_delta:,}\n"
              f"• **Vitórias:** {w_delta:,}V / {l_delta:,}D\n"
              f"• **WR Recente:** {wr_delta:.2f}%\n"
              f"• **Dano Médio Recente:** {avg_dmg_delta:.0f}"
          )
        else:
          periodo_txt = (
              f"Registro atualizado para **{player_name}**!\n"
              "*Jogue novas partidas e consulte novamente para ver a variação.*"
          )

        wr_all = (curr_wins / curr_battles * 100) if curr_battles > 0 else 0
        avg_dmg_all = (
            (curr_damage / curr_battles) if curr_battles > 0 else 0
        )

        geral_txt = (
            f"• **Total de Batalhas:** {curr_battles:,}\n"
            f"• **WR Geral:** {wr_all:.2f}%\n"
            f"• **Dano Médio Geral:** {avg_dmg_all:.0f}"
        )

        embed = discord.Embed(
            title=f"Estatísticas de {player_name} [{region_code.upper()}]",
            color=0x3498DB,
        )
        embed.add_field(
            name=f"📊 Desempenho Recente ({period_label})",
            value=periodo_txt,
            inline=False,
        )
        embed.add_field(
            name="🏆 Carreira (Geral)", value=geral_txt, inline=False
        )
        embed.set_footer(text="Tracker de Deltas via Wargaming API")
        await loading_msg.edit(content="", embed=embed)

    # --- OPÇÃO 2: ESTATÍSTICAS POR TANQUE COM SUB-INTERFACE ---
    elif opcao == "2":
      await ctx.send("Qual é o **nickname** do jogador?")
      msg_nick = await bot.wait_for("message", check=check, timeout=90.0)
      nickname = msg_nick.content.strip()

      await ctx.send(
          "Qual é o **nome do tanque**?\n*Exemplo: `T49`, `FV4005`, `WZ-122` ou"
          " `Tiger`*"
      )
      msg_tank = await bot.wait_for("message", check=check, timeout=90.0)
      search_tank_name = msg_tank.content.strip().lower()

      # Sub-interface de escolha de modo
      await ctx.send(
          "Qual **visualização** você deseja para este tanque?\n"
          "1️⃣ **Histórico Integral (Carreira)** — Estatísticas totais com o tanque\n"
          "2️⃣ **Jogados no Dia (Últimas 24h)** — Partidas jogadas hoje com o tanque\n\n"
          "*Digite `1` ou `2`:*"
      )
      msg_modo = await bot.wait_for("message", check=check, timeout=90.0)
      modo_tanque = msg_modo.content.strip()

      loading_msg = await ctx.send(
          f"🔍 Buscando o tanque **{msg_tank.content}** na conta de"
          f" **{nickname}**..."
      )

      async with aiohttp.ClientSession() as session:
        region_code, base_url, account_id, player_name = await find_player(
            nickname, session
        )
        if not account_id:
          await loading_msg.edit(
              content=f"❌ Jogador **{nickname}** não foi encontrado."
          )
          return

        encyclopedia = await get_tank_encyclopedia(base_url, session)
        if not encyclopedia:
          await loading_msg.edit(
              content="❌ Não foi possível carregar a base de tanques no momento."
          )
          return

        clean_search = (
            search_tank_name.replace("-", "").replace(" ", "").replace("_", "")
        )

        matching_tanks = {}
        for t_id, t_info in encyclopedia.items():
          t_name = t_info.get("name", "")
          clean_name = (
              t_name.lower().replace("-", "").replace(" ", "").replace("_", "")
          )
          if clean_search in clean_name:
            matching_tanks[int(t_id)] = t_info

        if not matching_tanks:
          await loading_msg.edit(
              content=(
                  f"❌ Nenhum tanque encontrado com o nome"
                  f" **'{msg_tank.content}'**."
              )
          )
          return

        tanks_url = f"{base_url}/wotb/tanks/stats/?application_id={APPLICATION_ID}&account_id={account_id}"
        async with session.get(
            tanks_url, timeout=aiohttp.ClientTimeout(total=12)
        ) as resp:
          t_json = await resp.json()
          user_tanks = t_json.get("data", {}).get(str(account_id), []) or []

        found_stats = []
        for u_tank in user_tanks:
          tank_id = u_tank.get("tank_id")
          if tank_id in matching_tanks:
            tank_info = matching_tanks[tank_id]
            stats = u_tank.get("all", {})
            found_stats.append({
                "tank_id": tank_id,
                "name": tank_info.get("name"),
                "tier": tank_info.get("tier"),
                "type": tank_info.get("type"),
                "icon": tank_info.get("images", {}).get("preview_image"),
                "battles": stats.get("battles", 0),
                "wins": stats.get("wins", 0),
                "damage": stats.get("damage_dealt", 0),
                "shots": stats.get("shots", 0),
                "hits": stats.get("hits", 0),
                "frags": stats.get("frags", 0),
            })

        if not found_stats:
          await loading_msg.edit(
              content=(
                  f"❌ **{player_name}** foi encontrado, mas **nunca jogou"
                  f" batalhas** com o tanque **{msg_tank.content}**."
              )
          )
          return

        found_stats.sort(key=lambda x: x["battles"], reverse=True)
        selected_tank = found_stats[0]
        t_id = selected_tank["tank_id"]

        tank_type_map = {
            "lightTank": "Tanque Leve ⚡",
            "mediumTank": "Tanque Médio 🎯",
            "heavyTank": "Tanque Pesado 🛡️",
            "AT-SPG": "Caça-Tanques 💥",
        }
        tipo_str = tank_type_map.get(
            selected_tank["type"], selected_tank["type"]
        )

        b_curr = selected_tank["battles"]
        w_curr = selected_tank["wins"]
        d_curr = selected_tank["damage"]

        # EXIBIÇÃO: JOGADOS NO DIA (24H)
        if modo_tanque == "2":
          b_delta, w_delta, d_delta = get_tank_delta(
              account_id, t_id, 1, b_curr, w_curr, d_curr
          )
          save_tank_snapshot(account_id, t_id, b_curr, w_curr, d_curr)

          embed = discord.Embed(
              title=(
                  f"📅 Estatísticas do Dia: {selected_tank['name']} (Tier"
                  f" {selected_tank['tier']})"
              ),
              description=(
                  f"Jogador: **{player_name}** [{region_code.upper()}]\nTipo:"
                  f" **{tipo_str}**"
              ),
              color=0xE67E22,
          )

          if b_delta > 0:
            l_delta = b_delta - w_delta
            wr_delta = (w_delta / b_delta) * 100
            avg_dmg_delta = d_delta / b_delta
            embed.add_field(
                name="📊 Desempenho nas Últimas 24h",
                value=(
                    f"• **Batalhas do Dia:** {b_delta:,}\n"
                    f"• **Vitórias:** {w_delta:,}V / {l_delta:,}D\n"
                    f"• **Winrate Hoje:** {wr_delta:.2f}%\n"
                    f"• **Dano Médio Hoje:** {avg_dmg_delta:.0f}"
                ),
                inline=False,
            )
          else:
            embed.add_field(
                name="📊 Desempenho nas Últimas 24h",
                value=(
                    "Nenhuma nova partida registrada com este tanque hoje.\n"
                    "*O registro do tanque foi salvo! Jogue com ele e consulte"
                    " novamente para ver a variação.*"
                ),
                inline=False,
            )

        # EXIBIÇÃO: HISTÓRICO INTEGRAL (PADRÃO)
        else:
          save_tank_snapshot(account_id, t_id, b_curr, w_curr, d_curr)

          l_curr = b_curr - w_curr
          wr_curr = (w_curr / b_curr * 100) if b_curr > 0 else 0
          avg_dmg_curr = (d_curr / b_curr) if b_curr > 0 else 0
          accuracy = (
              (selected_tank["hits"] / selected_tank["shots"] * 100)
              if selected_tank["shots"] > 0
              else 0
          )
          avg_frags = (selected_tank["frags"] / b_curr) if b_curr > 0 else 0

          embed = discord.Embed(
              title=(
                  f"🏆 Carreira Total: {selected_tank['name']} (Tier"
                  f" {selected_tank['tier']})"
              ),
              description=(
                  f"Jogador: **{player_name}** [{region_code.upper()}]\nTipo:"
                  f" **{tipo_str}**"
              ),
              color=0xE74C3C,
          )
          embed.add_field(
              name="📊 Desempenho Histórico Acumulado",
              value=(
                  f"• **Batalhas Totais:** {b_curr:,}\n"
                  f"• **Vitórias:** {w_curr:,}V / {l_curr:,}D\n"
                  f"• **Taxa de Vitórias (WR):** {wr_curr:.2f}%\n"
                  f"• **Dano Médio:** {avg_dmg_curr:.0f}\n"
                  f"• **Precisão dos Tiros:** {accuracy:.1f}%\n"
                  f"• **Média de Abates/Partida:** {avg_frags:.2f}"
              ),
              inline=False,
          )

        if selected_tank["icon"]:
          embed.set_thumbnail(url=selected_tank["icon"])

        embed.set_footer(
            text="Dados processados via Wargaming Official API & Delta Tracker"
        )
        await loading_msg.edit(content="", embed=embed)

    # --- OPÇÃO 3: CALCULADORA DE WINRATE ---
    elif opcao == "3":
      await ctx.send("🧮 **Calculadora de Meta de Winrate**")

      await ctx.send("1️⃣ Quantas **batalhas no total** você tem atualmente?")
      msg1 = await bot.wait_for("message", check=check, timeout=90.0)
      try:
        partidas_atuais = int(msg1.content.strip().replace(",", ""))
      except ValueError:
        await ctx.send("❌ Digite apenas números inteiros. Exemplo: `1000`")
        return

      await ctx.send(
          "2️⃣ Qual é a sua **Taxa de Vitórias (Winrate) atual** em %?\n*Exemplo:"
          " `47.5`*"
      )
      msg2 = await bot.wait_for("message", check=check, timeout=90.0)
      try:
        winrate_atual = float(
            msg2.content.strip().replace("%", "").replace(",", ".")
        )
      except ValueError:
        await ctx.send("❌ Digite um número válido. Exemplo: `47.5`")
        return

      await ctx.send(
          "3️⃣ Qual **Winrate recente** você pretende manter a partir de agora"
          " em %?\n*Exemplo: `53`*"
      )
      msg3 = await bot.wait_for("message", check=check, timeout=90.0)
      try:
        winrate_recente = float(
            msg3.content.strip().replace("%", "").replace(",", ".")
        )
      except ValueError:
        await ctx.send("❌ Digite um número válido. Exemplo: `53`")
        return

      await ctx.send(
          "4️⃣ Qual é a sua **Meta de Winrate (Alvo)** em %?\n*Exemplo: `50`*"
      )
      msg4 = await bot.wait_for("message", check=check, timeout=90.0)
      try:
        winrate_alvo = float(
            msg4.content.strip().replace("%", "").replace(",", ".")
        )
      except ValueError:
        await ctx.send("❌ Digite um número válido. Exemplo: `50`")
        return

      p_atual = winrate_atual / 100
      p_recente = winrate_recente / 100
      p_alvo = winrate_alvo / 100

      if p_recente <= p_alvo:
        await ctx.send(
            "❌ **Sua taxa recente precisa ser MAIOR que a taxa alvo** para"
            " subir seu WR geral."
        )
        return

      if p_alvo <= p_atual:
        await ctx.send(
            "❌ **A taxa alvo precisa ser MAIOR que a sua taxa atual.**"
        )
        return

      partidas_restantes = math.ceil(
          partidas_atuais * (p_alvo - p_atual) / (p_recente - p_alvo)
      )
      total_final = partidas_atuais + partidas_restantes

      embed = discord.Embed(title="🧮 Meta de Winrate", color=0x2ECC71)
      embed.add_field(
          name="📌 Dados Informados",
          value=(
              f"• Partidas Atuais: **{partidas_atuais:,}**\n"
              f"• WR Atual: **{winrate_atual:.2f}%**\n"
              f"• WR Recente Assumido: **{winrate_recente:.2f}%**\n"
              f"• WR Alvo: **{winrate_alvo:.2f}%**"
          ),
          inline=False,
      )
      embed.add_field(
          name="🎯 Resultado",
          value=(
              f"• Partidas Faltantes: **{partidas_restantes:,}**\n"
              f"• Total de Partidas ao Final: **{total_final:,}**"
          ),
          inline=False,
      )
      embed.set_footer(
          text=f"Mantendo {winrate_recente:.1f}% de taxa recente constantes."
      )

      await ctx.send(embed=embed)

    else:
      await ctx.send(
          "❌ Opção inválida! Execute `!blitz` novamente e escolha `1`, `2` ou"
          " `3`."
      )

  except asyncio.TimeoutError:
    await ctx.send(
        "⏰ **Tempo esgotado!** Digite `!blitz` para tentar novamente."
    )
  except Exception as e:
    await ctx.send(f"❌ Ocorreu um erro ao processar: `{e}`")


# --- 4. INICIALIZAÇÃO ---
keep_alive()
bot.run(DISCORD_TOKEN)
