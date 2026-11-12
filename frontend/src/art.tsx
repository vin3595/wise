/** Food artwork for menu cards: the item's own photo, or an illustrated tile until one is uploaded. */
const ICONS: [RegExp, string][] = [
  [/shake|smoothie|milk|frapp|lassi/i, "🥤"], [/coffee|latte|cappuccino|espresso|mocha/i, "☕"], [/tea|chai/i, "🍵"],
  [/ice ?cream|sundae|scoop|kulfi|gelato/i, "🍨"], [/waffle/i, "🧇"], [/brownie|cake|pastry|velvet|cookie/i, "🍰"],
  [/fries|wedges/i, "🍟"], [/burger/i, "🍔"], [/sandwich|club|sub/i, "🥪"], [/pizza/i, "🍕"], [/pasta/i, "🍝"],
  [/garlic bread|bread|toast/i, "🥖"], [/wrap|roll|taco/i, "🌯"], [/maggi|noodle/i, "🍜"], [/water/i, "💧"],
  [/mojito|lemon|soda|cooler|juice/i, "🍹"], [/mango/i, "🥭"], [/strawberr/i, "🍓"], [/chocolate|oreo|kitkat|choco/i, "🍫"],
];

export function itemIcon(name: string): string {
  return ICONS.find(([re]) => re.test(name))?.[1] ?? "🍽️";
}

export function ItemArt({ name, image, tint, size = "md" }: { name: string; image?: string | null; tint?: string | null; size?: "sm" | "md" | "lg" }) {
  if (image) return <img className={`art art-${size}`} src={image} alt="" loading="lazy" />;
  return (
    <span className={`art art-${size} art-drawn`} style={{ ["--tint" as string]: tint || "var(--brand)" }} aria-hidden="true">
      <span className="art-icon">{itemIcon(name)}</span>
    </span>
  );
}
