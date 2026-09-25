/**
 * Copy for the notifications the phone schedules itself (see smartNotify.ts).
 *
 * Placeholders, filled from the deal: {name} short product name, {price},
 * {mrp}, {save} (mrp - price), {discount} (number, no %), {brand},
 * {category}, {store}, {day} (Monday...).
 *
 * A template is only used when every field in `needs` exists on the deal, its
 * `time`/`day` match the moment it will fire, and (if set) the deal's category
 * is in `categories`. `personal` lines are only used when the deal matches
 * something this phone has shown real interest in. Playful, never a guess at
 * the user's mood.
 */

export type TimeOfDay = 'morning' | 'afternoon' | 'evening' | 'night';
export type Need = 'price' | 'mrp' | 'discount' | 'brand' | 'store' | 'endsSoon';

export type Template = {
  id: string;
  title: string;
  body: string;
  time?: TimeOfDay[];
  day?: 'weekday' | 'weekend';
  weekdays?: number[]; // 0 = Sunday ... 6 = Saturday
  needs?: Need[];
  categories?: string[];
  kinds?: string[];
  personal?: boolean;
};

const WOMEN = ['Women Fashion'];
const MEN = ['Men Fashion'];
const BEAUTY = ['Beauty'];
const TECH = ['Electronics', 'Appliances'];
const HOME = ['Home & Kitchen', 'Grocery'];
const FEET = ['Footwear'];
const KIDS = ['Baby & Kids'];
const FIT = ['Sports & Fitness'];
const BAGS = ['Bags & Luggage'];
const BOOKS = ['Books & Stationery'];

export const TEMPLATES: Template[] = [
  // ---- evergreen
  { id: 'g01', title: 'Psst… this one looks good 👀', body: '{name} — worth a quick look.' },
  { id: 'g02', title: 'Found something for you 🛍️', body: '{name}. Tap before someone else does.' },
  { id: 'g03', title: 'Your deal radar just beeped 📡', body: '{name} is live right now.' },
  { id: 'g04', title: 'Not an ad. Just a really good deal.', body: '{name} — go see for yourself.' },
  { id: 'g05', title: 'Quick one 👇', body: '{name}. Thank us later.' },
  { id: 'g06', title: 'This made the cut ✂️', body: 'Out of hundreds of deals today, {name} stood out.' },
  { id: 'g07', title: 'Plot twist: something useful 🎬', body: '{name} is on offer right now.' },
  { id: 'g08', title: 'Your cart would like a word 🛒', body: '{name} — just saying.' },
  { id: 'g09', title: 'Low effort, high reward 🏆', body: 'One tap to check {name}.' },
  { id: 'g10', title: 'We sifted. You win. 🔍', body: '{name} passed our spam filter and our taste test.' },
  { id: 'g11', title: 'Small treat, big smile 😌', body: '{name} — go on, have a look.' },
  { id: 'g12', title: 'Hot off the deal press 🔥', body: '{name} just landed.' },

  // ---- price
  { id: 'p01', title: 'Only {price}. Seriously. 🤯', body: '{name}', needs: ['price'] },
  { id: 'p02', title: '{price} well spent 💸', body: '{name} — about as good as it gets.', needs: ['price'] },
  { id: 'p03', title: 'Cheaper than your weekend snacks 🍿', body: '{name} for just {price}.', needs: ['price'] },
  { id: 'p04', title: 'Under {price}? Yes please.', body: '{name} is going for a steal.', needs: ['price'] },
  { id: 'p05', title: 'Wallet-friendly alert 👛', body: '{name} at {price}. Your wallet approves.', needs: ['price'] },
  { id: 'p06', title: '{price} and it’s yours ✨', body: '{name}', needs: ['price'] },
  { id: 'p07', title: 'Price check: {price} ✅', body: 'We checked. {name} really is this cheap.', needs: ['price'] },
  { id: 'p08', title: 'Less than a pizza 🍕', body: '{name} for {price}. Priorities.', needs: ['price'] },

  // ---- discount / savings
  { id: 'd01', title: '{discount}% OFF. Read that again. 😳', body: '{name}', needs: ['discount'] },
  { id: 'd02', title: 'Somebody slashed this by {discount}% 🔪', body: '{name} — now {price}.', needs: ['discount', 'price'] },
  { id: 'd03', title: '{discount}% off? Don’t mind if we do.', body: '{name}', needs: ['discount'] },
  { id: 'd04', title: 'You save {save} on this 🤑', body: '{name} — was {mrp}, now {price}.', needs: ['mrp', 'price'] },
  { id: 'd05', title: 'From {mrp} to {price} 📉', body: '{name}. That’s a real drop.', needs: ['mrp', 'price'] },
  { id: 'd06', title: 'Math is fun when it’s {discount}% off 🧮', body: '{name} for {price}.', needs: ['discount', 'price'] },
  { id: 'd07', title: 'Keep {save} in your pocket 💰', body: '{name} is heavily marked down right now.', needs: ['mrp', 'price'] },
  { id: 'd08', title: 'Big discount energy ⚡', body: '{name} — {discount}% off.', needs: ['discount'] },
  { id: 'd09', title: '{discount}% off and still classy 💅', body: '{name}', needs: ['discount'] },
  { id: 'd10', title: 'MRP who? 😎', body: '{name} is {discount}% below it.', needs: ['discount'] },

  // ---- brand / store
  { id: 'b01', title: '{brand} on sale 👀', body: '{name} — {price}.', needs: ['brand', 'price'] },
  { id: 'b02', title: '{brand} fans, assemble 🙌', body: '{name} just dropped in price.', needs: ['brand'] },
  { id: 'b03', title: 'Branded, not basic ✨', body: '{brand}: {name}', needs: ['brand'] },
  { id: 'b04', title: '{brand} for less? Say less.', body: '{name}', needs: ['brand'] },
  { id: 's01', title: 'Spotted on {store} 🔎', body: '{name} — {price}.', needs: ['store', 'price'] },
  { id: 's02', title: '{store} is feeling generous today 🎁', body: '{name}', needs: ['store'] },
  { id: 's03', title: 'Straight from {store} 📦', body: '{name} at a price that won’t last.', needs: ['store'] },

  // ---- ending soon
  { id: 'e01', title: 'Tick tock ⏳', body: '{name} ends soon. Just saying.', needs: ['endsSoon'] },
  { id: 'e02', title: 'Last call for this one 📣', body: '{name} is about to expire.', needs: ['endsSoon'] },
  { id: 'e03', title: 'Going, going… 🏃', body: '{name} won’t be around much longer.', needs: ['endsSoon'] },
  { id: 'e04', title: 'Now or never (well, now or soon) ⌛', body: '{name} — ending shortly.', needs: ['endsSoon'] },

  // ---- personal (only when it matches what this phone keeps looking at)
  { id: 'u01', title: 'You keep checking {category}… 👀', body: 'So we found {name} for you.', personal: true },
  { id: 'u02', title: 'Picked just for you 🎯', body: 'Based on what you like: {name}.', personal: true },
  { id: 'u03', title: 'Your kind of deal 😉', body: '{name} — right up your street.', personal: true },
  { id: 'u04', title: 'We know your taste 😌', body: '{name} is very you.', personal: true },
  { id: 'u05', title: 'More {category}? Obviously.', body: '{name} at {price}.', personal: true, needs: ['price'] },
  { id: 'u06', title: 'This one screamed your name 📢', body: '{name}', personal: true },
  { id: 'u07', title: 'Based on your recent browsing 🧭', body: '{name} is live now.', personal: true },
  { id: 'u08', title: 'You + {brand} = ❤️', body: '{name} is on offer.', personal: true, needs: ['brand'] },
  { id: 'f01', title: 'New in {brand} (you follow it) 🔔', body: '{name}', kinds: ['follow'], needs: ['brand'] },
  { id: 'f02', title: 'Something new in what you follow ⭐', body: '{name} — {price}.', kinds: ['follow'], needs: ['price'] },

  // ---- time of day
  { id: 'm01', title: 'Good morning ☀️ Deal with your chai?', body: '{name}', time: ['morning'] },
  { id: 'm02', title: 'Rise and shop 🌅', body: '{name} is waiting.', time: ['morning'] },
  { id: 'm03', title: 'Early bird gets the deal 🐦', body: '{name} — before the crowd wakes up.', time: ['morning'] },
  { id: 'm04', title: 'Coffee’s brewing, prices are dropping ☕', body: '{name}', time: ['morning'] },
  { id: 'm05', title: 'Start the day with a win 🏁', body: '{name} for {price}.', time: ['morning'], needs: ['price'] },
  { id: 'a01', title: 'Lunch break scroll? We got you 🍱', body: '{name}', time: ['afternoon'] },
  { id: 'a02', title: 'Afternoon slump cure 💊', body: '{name} — much better than a meeting.', time: ['afternoon'] },
  { id: 'a03', title: 'Post-lunch deal drop 🥪', body: '{name} for {price}.', time: ['afternoon'], needs: ['price'] },
  { id: 'a04', title: 'Quick peek between tasks? 👀', body: '{name}', time: ['afternoon'] },
  { id: 'v01', title: 'Evening loot unlocked 🌆', body: '{name}', time: ['evening'] },
  { id: 'v02', title: 'Chai, snacks, and a deal 🫖', body: '{name} — the perfect evening combo.', time: ['evening'] },
  { id: 'v03', title: 'Done for the day? Treat yourself 🎉', body: '{name} for {price}.', time: ['evening'], needs: ['price'] },
  { id: 'v04', title: 'Sunset sale energy 🌇', body: '{name}', time: ['evening'] },
  { id: 'n01', title: 'Night owl detected 🦉', body: 'Here’s a midnight loot: {name}.', time: ['night'] },
  { id: 'n02', title: 'Can’t sleep? Neither can these prices 🌙', body: '{name}', time: ['night'] },
  { id: 'n03', title: 'Late-night find 🔦', body: '{name} for {price}. Sweet dreams.', time: ['night'], needs: ['price'] },
  { id: 'n04', title: 'Shh… secret night deal 🤫', body: '{name}', time: ['night'] },
  { id: 'n05', title: 'One last scroll? Make it count ✨', body: '{name}', time: ['night'] },

  // ---- day of week
  { id: 'w01', title: 'Weekend mode: ON 🛋️', body: '{name} — perfect for a lazy day.', day: 'weekend' },
  { id: 'w02', title: 'Saturday shopping, sorted ✅', body: '{name}', weekdays: [6] },
  { id: 'w03', title: 'Sunday funday deal 🌞', body: '{name} for {price}.', weekdays: [0], needs: ['price'] },
  { id: 'w04', title: 'Weekend treat unlocked 🔓', body: '{name}', day: 'weekend' },
  { id: 'w05', title: 'Monday needs a little retail therapy 😮‍💨', body: '{name} — just saying.', weekdays: [1] },
  { id: 'w06', title: 'Midweek pick-me-up 🐪', body: '{name}', weekdays: [3] },
  { id: 'w07', title: 'Friday feeling + a deal 🎊', body: '{name} for {price}.', weekdays: [5], needs: ['price'] },
  { id: 'w08', title: 'Happy {day}! Here’s a gift 🎁', body: '{name}' },
  { id: 'w09', title: 'Weekday win 💼', body: '{name} — small joy, big savings.', day: 'weekday' },

  // ---- women fashion
  { id: 'cw1', title: 'Your wardrobe called 📞', body: 'It wants {name}. For {price}.', categories: WOMEN, needs: ['price'] },
  { id: 'cw2', title: 'Outfit sorted ✅', body: '{name} — {discount}% off.', categories: WOMEN, needs: ['discount'] },
  { id: 'cw3', title: 'Main character outfit 💃', body: '{name}', categories: WOMEN },
  { id: 'cw4', title: 'Closet upgrade incoming 👗', body: '{name} at {price}.', categories: WOMEN, needs: ['price'] },
  { id: 'cw5', title: 'Twirl-worthy deal 🌸', body: '{name}', categories: WOMEN },
  { id: 'cw6', title: '“Where did you get that?” — everyone', body: '{name}. Only you will know it was a steal.', categories: WOMEN },

  // ---- men fashion
  { id: 'cm1', title: 'Fit check: passed ✅', body: '{name} for {price}.', categories: MEN, needs: ['price'] },
  { id: 'cm2', title: 'Look sharp, spend less 🕶️', body: '{name}', categories: MEN },
  { id: 'cm3', title: 'Your wardrobe wants a promotion 👔', body: '{name} — {discount}% off.', categories: MEN, needs: ['discount'] },
  { id: 'cm4', title: 'Drip at a discount 💧', body: '{name}', categories: MEN },

  // ---- beauty
  { id: 'cb1', title: 'Glow-up budget approved ✨', body: '{name} for {price}.', categories: BEAUTY, needs: ['price'] },
  { id: 'cb2', title: 'Self-care, now cheaper 🧴', body: '{name}', categories: BEAUTY },
  { id: 'cb3', title: 'Smell nice, pay less 🌷', body: '{name} — {discount}% off.', categories: BEAUTY, needs: ['discount'] },
  { id: 'cb4', title: 'Your shelf has room for this 💄', body: '{name}', categories: BEAUTY },

  // ---- electronics / appliances
  { id: 'ct1', title: 'Gadget alert 🔌', body: '{name} for {price}.', categories: TECH, needs: ['price'] },
  { id: 'ct2', title: 'Upgrade season 📱', body: '{name} — {discount}% off.', categories: TECH, needs: ['discount'] },
  { id: 'ct3', title: 'Tech that won’t break the bank 💻', body: '{name}', categories: TECH },
  { id: 'ct4', title: 'Your old one had a good run 🪦', body: 'Time for {name} at {price}.', categories: TECH, needs: ['price'] },
  { id: 'ct5', title: 'Beep boop, good deal 🤖', body: '{name}', categories: TECH },

  // ---- home & kitchen / grocery
  { id: 'ch1', title: 'Home glow-up 🏠', body: '{name} for {price}.', categories: HOME, needs: ['price'] },
  { id: 'ch2', title: 'Adulting, but cheaper 🧺', body: '{name}', categories: HOME },
  { id: 'ch3', title: 'Your kitchen will thank you 🍳', body: '{name} — {discount}% off.', categories: HOME, needs: ['discount'] },
  { id: 'ch4', title: 'Stock up time 🛒', body: '{name} at {price}.', categories: HOME, needs: ['price'] },

  // ---- footwear
  { id: 'cf1', title: 'New kicks, who dis? 👟', body: '{name} for {price}.', categories: FEET, needs: ['price'] },
  { id: 'cf2', title: 'Step up your game 🦶', body: '{name} — {discount}% off.', categories: FEET, needs: ['discount'] },
  { id: 'cf3', title: 'Your feet deserve this 👠', body: '{name}', categories: FEET },

  // ---- kids, fitness, bags, books
  { id: 'ck1', title: 'Little ones, big savings 🧸', body: '{name} for {price}.', categories: KIDS, needs: ['price'] },
  { id: 'ck2', title: 'Parenting hack: this deal 🍼', body: '{name}', categories: KIDS },
  { id: 'cs1', title: 'Gains, not pains 💪', body: '{name} for {price}.', categories: FIT, needs: ['price'] },
  { id: 'cs2', title: 'Skip the excuse, grab the gear 🏋️', body: '{name}', categories: FIT },
  { id: 'cg1', title: 'Pack your bags (literally) 🎒', body: '{name} for {price}.', categories: BAGS, needs: ['price'] },
  { id: 'cg2', title: 'Travel ready, wallet happy ✈️', body: '{name}', categories: BAGS },
  { id: 'cr1', title: 'Bookworm alert 📚', body: '{name} for {price}.', categories: BOOKS, needs: ['price'] },
  { id: 'cr2', title: 'Smart buy, literally 🤓', body: '{name}', categories: BOOKS },

  // ---- FOMO / social proof
  { id: 'o01', title: 'Everyone’s posting this one 📣', body: '{name} is showing up across deal channels.', kinds: ['hot_deal'] },
  { id: 'o02', title: 'Trending right now 📈', body: '{name}', kinds: ['hot_deal'] },
  { id: 'o03', title: 'Don’t say we didn’t tell you 🤷', body: '{name} for {price}.', needs: ['price'] },
  { id: 'o04', title: 'Your group chat would want to know 💬', body: '{name} — share it or keep it secret.' },
  { id: 'o05', title: 'Blink and it’s gone 👁️', body: '{name}' },
  { id: 'o06', title: 'Today’s top pick 🥇', body: '{name} for {price}.', kinds: ['digest', 'weekly_pick'], needs: ['price'] },
];
