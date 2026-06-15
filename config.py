import os

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
MONGO_URL: str = os.environ["MONGO_URL"]

ADMIN_IDS: list[int] = [
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]

DUMMY_PORT: int = int(os.environ.get("PORT", "10000"))

ALIASES: list[str] = [
    "Fox 🦊", "Panda 🐼", "Wolf 🐺", "Rose 🌹", "Tiger 🐯", "Owl 🦉",
    "Deer 🦌", "Bunny 🐰", "Bear 🐻", "Tulip 🌷", "Hawk 🦅", "Lynx 🐆",
    "Otter 🦦", "Raven 🐦‍⬛", "Lily 🌸", "Crane 🦢", "Viper 🐍", "Koala 🐨",
    "Peony 🌺", "Bison 🦬", "Coyote 🐺", "Heron 🕊️", "Orchid 💐", "Gecko 🦎",
    "Sparrow 🐦", "Hyena 🐗", "Lotus 🪷", "Meerkat 🐾", "Flamingo 🦩",
    "Marigold 🌻", "Jackal 🦌", "Ibis 🦤", "Peregrine 🦅", "Clover 🍀",
    "Badger 🦡", "Narwhal 🐳", "Wisteria 🌿", "Capybara 🦫", "Egret 🦢",
    "Dahlia 🌼", "Stoat 🐀", "Kestrel 🦜", "Jasmine 🌾", "Mantis 🦗",
    "Sunflower 🌻", "Puffin 🐧", "Iris 🪻", "Dingo 🐕", "Macaw 🦜", "Fern 🌿",
]
