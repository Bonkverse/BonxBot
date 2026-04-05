import aiohttp

API_URL = "https://blskinfetcher-production.up.railway.app/api/getSkin"


async def fetch_skin_data(link: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL, params={"link": link}) as resp:
                if resp.status != 200:
                    print(f"[SkinFetcher] Bad status: {resp.status}")
                    return None

                data = await resp.json()
                print(f"[SkinFetcher] Response: {data}")
                return data

    except Exception as e:
        print(f"[SkinFetcher ERROR] {e}")
        return None