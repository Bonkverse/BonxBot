import aiohttp

API_URL = "https://blskinfetcher-production.up.railway.app/api/getSkin"


async def fetch_skin_data(link: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL, params={"link": link}) as resp:
            if resp.status != 200:
                return None

            data = await resp.json()
            return data