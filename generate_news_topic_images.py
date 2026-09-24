import hashlib
import os
import time
from urllib.parse import quote

import requests
from supabase import create_client


SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or "").strip()
if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY is required")

TOPICS = {
    "global_markets": "global financial markets, stock exchanges, world finance, currencies, institutional investing and city skylines",
    "interest_rates": "central banking, interest rates, inflation, monetary policy, rate-setting institutions and financial markets",
    "bonds_yields": "government bonds, treasury securities, bond yields, fixed income trading, institutional debt markets and yield curves",
    "dollar_fx": "US dollar, Korean won, foreign exchange trading, currency markets, exchange rates and international finance",
    "energy_oil": "oil, natural gas, refineries, pipelines, tankers, pumpjacks, energy commodities and industrial infrastructure",
    "ai_semiconductors": "AI computing, advanced semiconductors, chip fabrication, wafers, data centers, processors and technology finance",
    "trade_global": "global trade, cargo ships, shipping containers, ports, cranes, factories, manufacturing and supply chains",
    "korea_asia": "South Korea and Asian finance, Seoul skyline, Asian stock markets, semiconductor industry, ports and regional trade",
    "economy_jobs": "economic activity, employment, wages, consumer spending, offices, factories and business activity",
    "crypto_assets": "digital assets, blockchain infrastructure, cryptocurrency markets, secure digital finance and distributed networks",
}

VARIANTS = (
    "cinematic wide establishing shot, premium financial magazine photography, realistic daylight",
    "close-up editorial still life, realistic materials, shallow depth of field, sophisticated newsroom lighting",
    "dynamic documentary-style business scene, natural perspective, crisp details, premium institutional aesthetic",
)

MODEL = "flux-2-klein-4b"
WIDTH = 1536
HEIGHT = 864


def build_url(topic_key: str, variant_index: int) -> str:
    seed_source = f"fundamental-news-topic|{topic_key}|{variant_index}"
    seed = int(hashlib.sha256(seed_source.encode("utf-8")).hexdigest()[:8], 16)
    prompt = (
        "Create a premium high-resolution 16:9 editorial photograph for a professional financial news website. "
        "Photorealistic, realistic lighting, crisp fine detail, natural depth, strong but clean composition. "
        "No readable text, no captions, no watermarks, no logos, no fake charts with text, no recognizable real people. "
        "One coherent scene only, not a collage, not a split screen, not a poster. "
        f"Sector subject: {TOPICS[topic_key]}. "
        f"Visual treatment: {VARIANTS[variant_index - 1]}."
    )
    return (
        "https://image.pollinations.ai/prompt/"
        + quote(prompt, safe="")
        + f"?model={MODEL}&width={WIDTH}&height={HEIGHT}&seed={seed}&nologo=true&enhance=true"
    )


def download_image(url: str):
    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(
                url,
                timeout=90,
                headers={"User-Agent": "FundamentalNewsTopicImageBot/1.0"},
                allow_redirects=True,
            )
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise RuntimeError(f"unexpected content type: {content_type}")
            data = response.content
            if not data or len(data) < 10_000 or len(data) > 8 * 1024 * 1024:
                raise RuntimeError(f"unexpected image size: {len(data)}")
            return data, content_type
        except Exception as exc:
            last_error = exc
            time.sleep(2 + attempt)
    raise RuntimeError(f"image generation failed after retries: {last_error}")


def ext_for(content_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }[content_type]


def main():
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    generated = 0
    failures = []

    for topic_key in TOPICS:
        for variant_index in range(1, 4):
            url = build_url(topic_key, variant_index)
            data, content_type = download_image(url)
            suffix = ext_for(content_type)
            storage_path = f"generated/{topic_key}_{variant_index:02d}{suffix}"
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/news-topic-images/{storage_path}"

            client.storage.from_("news-topic-images").upload(
                storage_path,
                data,
                {
                    "content-type": content_type,
                    "cache-control": "31536000",
                    "upsert": "true",
                },
            )
            client.table("news_topic_images").upsert(
                {
                    "topic_key": topic_key,
                    "storage_path": storage_path,
                    "public_url": public_url,
                    "width": WIDTH,
                    "height": HEIGHT,
                    "model": MODEL,
                },
                on_conflict="storage_path",
            ).execute()
            generated += 1
            print(f"[TOPIC IMAGE] {generated:02d}/30 {topic_key} variant={variant_index} -> {storage_path}")

    rows = (
        client.table("news_topic_images")
        .select("topic_key,storage_path,public_url")
        .execute()
        .data
        or []
    )
    counts = {}
    for row in rows:
        counts[row.get("topic_key")] = counts.get(row.get("topic_key"), 0) + 1

    print("[TOPIC IMAGE] final counts:", counts)
    missing = [topic for topic in TOPICS if counts.get(topic, 0) < 4]
    if missing:
        raise RuntimeError(f"topic image pool incomplete: {missing}")
    print("[TOPIC IMAGE] completed 30 new images; each topic now has at least 4 images")


if __name__ == "__main__":
    main()
