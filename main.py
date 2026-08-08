import asyncio
from datetime import datetime
import math
import os
import time
from threading import Thread
import aiohttp
import discord
from discord.ext import commands
from flask import Flask

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


# --- 2. CONFIGURAÇÃO DO BOT DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

APPLICATION_ID = os.environ.get("APPLICATION_ID")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")


def parse_timestamp(val):
  """Converte formatos de data (ISO string, int, float) para timestamp UNIX (segundos)."""
  if isinstance(val, (int, float)):
    return val / 1000.0 if val > 2000000000 else float(val)
  if isinstance(val, str):
    try:
      cleaned = val.replace("Z", "+00:00")
      return datetime.fromisoformat(cleaned).timestamp()
    except Exception:
      pass
  return None


async def find_player(nickname: str, session: aiohttp.ClientSession):
  """Busca o ID do jogador e região pela API oficial da Wargaming."""
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


@bot.event
async def on_ready():
  print(f"Bot conectado com sucesso como {bot.user}")


@bot.command()
async def blitz(ctx):
  """Menu interativo do bot."""

  def check(m):
    return m.author == ctx.author and m.channel == ctx.channel

  try:
    # --- MENU PRINCIPAL ---
    await ctx.send(
        "🎮 **Menu Blitz**\n"
        "O que você deseja fazer?\n"
        "1️⃣ Ver Estatísticas de um Jogador\n"
        "2️⃣ Calcular Meta de Winrate\n\n"
        "*Digite `1` ou `2`:*"
    )
    msg_opcao = await bot.wait_for("message", check=check, timeout=90.0)
    opcao = msg_opcao.content.strip()

    # --- FLUXO 1: ESTATÍSTICAS ---
    if opcao == "1":
      await ctx.send(
          "Qual é o **nickname** do jogador no World of Tanks Blitz?"
      )
      msg_nick = await bot.wait_for("message", check=check, timeout=90.0)
      nickname = msg_nick.content.strip()

      await ctx.send(
          "Escolha o **período** das estatísticas:\n"
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
          f"🔍 Buscando dados recentes de **{nickname}**..."
      )

      headers = {
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
              " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
          )
      }

      async with aiohttp.ClientSession(headers=headers) as session:
        region_code, base_url, account_id, player_name = await find_player(
            nickname, session
        )

        if not account_id:
          await loading_msg.edit(
              content=(
                  f"❌ Jogador **{nickname}** não foi encontrado nos"
                  " servidores NA, EU ou ASIA."
              )
          )
          return

        timeout = aiohttp.ClientTimeout(total=12)

        # 1. Busca os dados atuais direto da Wargaming
        info_url = f"{base_url}/wotb/account/info/?application_id={APPLICATION_ID}&account_id={account_id}"
        async with session.get(info_url, timeout=timeout) as resp:
          wg_data = await resp.json()
          player_info = wg_data.get("data", {}).get(str(account_id), {})
          stats_all = player_info.get("statistics", {}).get("all", {})

        curr_battles = stats_all.get("battles", 0)
        curr_wins = stats_all.get("wins", 0)
        curr_damage = stats_all.get("damage_dealt", 0)

        # 2. Força atualização da conta na base de dados
        try:
          await session.get(
              f"https://www.blitzstars.com/api/player/{account_id}",
              timeout=timeout,
          )
        except Exception:
          pass

        # 3. Requisita os snapshots do histórico
        bs_hist_url = (
            f"https://www.blitzstars.com/api/playerstats/{account_id}"
        )
        snapshots = []
        try:
          async with session.get(bs_hist_url, timeout=timeout) as resp:
            if resp.status == 200:
              raw_json = await resp.json()
              if isinstance(raw_json, list):
                snapshots = raw_json
              elif isinstance(raw_json, dict):
                snapshots = [raw_json]
        except Exception as e:
          print(f"Erro na busca do histórico: {e}")

        periodo_txt = None

        if snapshots and curr_battles > 0:
          now_ts = time.time()
          target_ts = now_ts - (days_limit * 86400)

          closest_snapshot = None
          smallest_diff = float("inf")

          for snap in snapshots:
            raw_date = (
                snap.get("createdAt")
                or snap.get("updatedAt")
                or snap.get("date")
            )
            snap_ts = parse_timestamp(raw_date)

            if snap_ts:
              diff = abs(snap_ts - target_ts)
              if diff < smallest_diff:
                smallest_diff = diff
                closest_snapshot = snap

          if closest_snapshot:
            # Extrai os dados do snapshot antigo
            old_all = closest_snapshot.get("all") or closest_snapshot.get(
                "statistics", {}
            ).get("all", {})
            old_battles = old_all.get("battles", 0)
            old_wins = old_all.get("wins", 0)
            old_damage = old_all.get("damage_dealt", 0)

            # Cálculo do Delta do Período
            battles_delta = curr_battles - old_battles
            wins_delta = curr_wins - old_wins
            damage_delta = curr_damage - old_damage

            if battles_delta > 0:
              losses_delta = battles_delta - wins_delta
              wr_delta = (wins_delta / battles_delta) * 100
              avg_dmg_delta = damage_delta / battles_delta

              periodo_txt = (
                  f"• **Batalhas:** {battles_delta:,}\n"
                  f"• **Vitórias:** {wins_delta:,}V / {losses_delta:,}D\n"
                  f"• **WR:** {wr_delta:.2f}%\n"
                  f"• **Dano Médio:** {avg_dmg_delta:.0f}"
              )

        if not periodo_txt:
          periodo_txt = (
              f"Nenhum histórico registrado para os últimos {period_label}.\n"
              "*(O perfil foi cadastrado agora no sistema e passará a registrar"
              " os deltas a partir das próximas partidas)*"
          )

        # Dados da Carreira
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
            name=f"📊 Desempenho ({period_label})",
            value=periodo_txt,
            inline=False,
        )
        embed.add_field(
            name="🏆 Carreira (Geral)", value=geral_txt, inline=False
        )
        embed.set_footer(text="Processado via Snapshots & Delta Tracker")

        await loading_msg.edit(content="", embed=embed)

    # --- FLUXO 2: CALCULADORA DE WINRATE ---
    elif opcao == "2":
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
          "❌ Opção inválida! Execute `!blitz` novamente e escolha `1` ou `2`."
      )

  except asyncio.TimeoutError:
    await ctx.send(
        "⏰ **Tempo esgotado!** Digite `!blitz` para tentar novamente."
    )
  except Exception as e:
    await ctx.send(f"❌ Ocorreu um erro ao processar: `{e}`")


# --- 3. INICIALIZAÇÃO ---
keep_alive()
bot.run(DISCORD_TOKEN)
