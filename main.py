async def get_tank_encyclopedia(base_url: str, session: aiohttp.ClientSession):
  """Busca a lista completa de tanques de forma rápida e com fallback."""
  # 1. Tentativa principal via BlitzStars (JSON direto, leve e completo)
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

  # 2. Fallback via Wargaming API (com parâmetro de campos específicos para não estourar tempo)
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
