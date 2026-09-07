import { useEffect, useState } from "react";
import type { ProductCard } from "../types";

// 旧会话在图片字段上线前已落库，仍会带着历史 tool.result 回放。
// 用同一批公开图片作类别回退，刷新后也能立刻摆脱字母占位图。
const FALLBACK_IMAGES: Record<string, string> = {
  "旅行装备": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=900&h=560&q=82",
  "数码配件": "https://images.unsplash.com/photo-1583863788434-e58a36330cf0?auto=format&fit=crop&w=900&h=560&q=82",
  "家居生活": "https://images.unsplash.com/photo-1495100497150-fe209c585f50?auto=format&fit=crop&w=900&h=560&q=82",
  "户外运动": "https://images.unsplash.com/photo-1504280390367-361c6d9f38f4?auto=format&fit=crop&w=900&h=560&q=82",
};

function imageUrl(card: ProductCard): string | undefined {
  return card.image_url || FALLBACK_IMAGES[card.category];
}

interface ProductCardsProps {
  groups: ProductCard[][];
  busy?: boolean;
  onOrder?: (card: ProductCard, skuId: string) => void;
}

export default function ProductCards({ groups, busy = false, onOrder }: ProductCardsProps) {
  const cards = groups.flat();
  const [selected, setSelected] = useState<Record<string, string>>({});

  useEffect(() => {
    setSelected((current) => {
      const next = { ...current };
      cards.forEach((card) => { if (!next[card.product_id]) next[card.product_id] = card.skus[0]?.sku_id; });
      return next;
    });
  }, [groups]);

  if (!groups.length) return null;

  return (
    <>
      {groups.map((group, groupIndex) => (
        <section key={`recommendation-${groupIndex}`} className="results-section in-chat-results">
          <div className="section-heading"><div><span className="eyebrow">MATCHED PRODUCTS</span><h2>{groups.length > 1 ? `第 ${groupIndex + 1} 次商品推荐` : "为你筛选的商品"}</h2></div><span className="result-count">{group.length} 个结果</span></div>
          <div className="cards">
        {group.map((card, index) => {
          const selectedSku = card.skus.find((sku) => sku.sku_id === selected[card.product_id]) ?? card.skus[0];
          const total = card.landed_price?.landed_total_major;
          const image = imageUrl(card);
          return (
            <article key={card.product_id} className="product-card">
              <div className="card-topline"><span className="rank">0{index + 1}</span><span className="match">TOP {index + 1} 推荐</span></div>
              <div className="product-visual">
                {image ? <img src={image} alt={card.title} loading="lazy" /> : <strong>{card.brand.slice(0, 1)}</strong>}
                <span>{card.category.includes("数码") ? "DIGITAL ACCESSORY" : "SHOPCROSS SELECT"}</span>
              </div>
              <div className="product-copy"><span className="origin">{card.brand} · {card.origin_country}</span><h3>{card.title}</h3><ul>{card.highlights.slice(0, 2).map((highlight) => <li key={highlight}>{highlight}</li>)}</ul></div>
              <div className="sku-picker">
                {card.skus.map((sku) => <button key={sku.sku_id} className={selectedSku?.sku_id === sku.sku_id ? "selected" : ""} onClick={() => setSelected((current) => ({ ...current, [card.product_id]: sku.sku_id }))}>{sku.spec}</button>)}
              </div>
              <div className="price-block">
                <div><small>商品价格</small><strong>{selectedSku?.price_major ?? card.price_major} <i>{selectedSku?.currency ?? card.currency}</i></strong></div>
                {total !== undefined && <div className="landed-total"><small>预计到手</small><strong>{total} <i>{card.landed_price?.currency}</i></strong></div>}
              </div>
              {card.landed_price && !card.landed_price.unavailable_reason && <div className="cost-line">商品 {card.landed_price.subtotal_major} + 运费 {card.landed_price.freight_major} + 关税 {card.landed_price.tariff_major}</div>}
              <div className="stock-line"><i /> {selectedSku?.stock ?? 0} 件现货</div>
              <button
                className="order-button"
                disabled={busy || !selectedSku || selectedSku.stock < 1}
                onClick={() => selectedSku && onOrder?.(card, selectedSku.sku_id)}
              >
                {selectedSku && selectedSku.stock > 0 ? "下单" : "暂时缺货"}
              </button>
            </article>
          );
        })}
          </div>
        </section>
      ))}
    </>
  );
}
