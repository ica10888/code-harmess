// 等待回复时显示的随机词，以及「渐进式披露」的切片逻辑。

export const WAITING_WORDS = [
  'Accomplishing', 'Actioning', 'Actualizing', 'Architecting', 'Baking', 'Beaming', 'Beboppin’', 'Befuddling',
  'Billowing', 'Blanching', 'Bloviating', 'Boogieing', 'Boondoggling', 'Booping', 'Bootstrapping', 'Brewing',
  'Bunning', 'Burrowing', 'Calculating', 'Canoodling', 'Caramelizing', 'Cascading', 'Catapulting', 'Cerebrating',
  'Channeling', 'Channelling', 'Choreographing', 'Churning', 'Clauding', 'Coalescing', 'Cogitating', 'Combobulating',
  'Composing', 'Computing', 'Concocting', 'Considering', 'Contemplating', 'Cooking', 'Crafting', 'Creating',
  'Crunching', 'Crystallizing', 'Cultivating', 'Deciphering', 'Deliberating', 'Determining', 'Dilly-dallying',
  'Discombobulating', 'Doing', 'Doodling', 'Drizzling', 'Ebbing', 'Effecting', 'Elucidating', 'Embellishing',
  'Enchanting', 'Envisioning', 'Evaporating', 'Fermenting', 'Fiddle-faddling', 'Finagling', 'Flambéing',
  'Flibbertigibbeting', 'Flowing', 'Flummoxing', 'Fluttering', 'Forging', 'Forming', 'Frolicking', 'Frosting',
  'Gallivanting', 'Galloping', 'Garnishing', 'Generating', 'Gesticulating', 'Germinating', 'Gitifying', 'Grooving',
  'Gusting', 'Harmonizing', 'Hashing', 'Hatching', 'Herding', 'Honking', 'Hullaballooing', 'Hyperspacing', 'Ideating',
  'Imagining', 'Improvising', 'Incubating', 'Inferring', 'Infusing', 'Ionizing', 'Jitterbugging', 'Julienning',
  'Kneading', 'Leavening', 'Levitating', 'Lollygagging', 'Manifesting', 'Marinating', 'Meandering',
  'Metamorphosing', 'Misting', 'Moonwalking', 'Moseying', 'Mulling', 'Mustering', 'Musing', 'Nebulizing', 'Nesting',
  'Newspapering', 'Noodling', 'Nucleating', 'Orbiting', 'Orchestrating', 'Osmosing', 'Perambulating', 'Percolating',
  'Perusing', 'Philosophising', 'Photosynthesizing', 'Pollinating', 'Pondering', 'Pontificating', 'Pouncing',
  'Precipitating', 'Prestidigitating', 'Processing', 'Proofing', 'Propagating', 'Puttering', 'Puzzling',
  'Quantumizing', 'Razzle-dazzling', 'Razzmatazzing', 'Recombobulating', 'Reticulating', 'Roosting', 'Ruminating',
  'Sautéing', 'Scampering', 'Schlepping', 'Scurrying', 'Seasoning', 'Shenaniganing', 'Shimmying', 'Simmering',
  'Skedaddling', 'Sketching', 'Slithering', 'Smooshing', 'Sock-hopping', 'Spelunking', 'Spinning', 'Sprouting',
  'Stewing', 'Sublimating', 'Swirling', 'Swooping', 'Symbioting', 'Synthesizing', 'Tempering', 'Thinking',
  'Thundering', 'Tinkering', 'Tomfoolering', 'Topsy-turvying', 'Transfiguring', 'Transmuting', 'Twisting',
  'Undulating', 'Unfurling', 'Unravelling', 'Vibing', 'Waddling', 'Wandering', 'Warping', 'Whatchamacalliting',
  'Whirlpooling', 'Whirring', 'Whisking', 'Wibbling', 'Working', 'Wrangling', 'Zesting', 'Zigzagging',
]

export const DOTS = '...'

/** 随机挑一个词；random 可注入，方便测试。exclude 是要避开的词（换词时不重复上一个）。 */
export function pickWord(random = Math.random, exclude = null) {
  const pool = exclude ? WAITING_WORDS.filter((w) => w !== exclude) : WAITING_WORDS
  return pool[Math.floor(random() * pool.length)]
}

/**
 * 渐进式披露：step 从 0 开始，每步多露出一个字；字露完后，再一个一个露出 "..."。
 * 按码点切，不拆开带重音符等的字符之外的组合（Array.from 保证 emoji 不被截断）。
 */
export function revealStep(word, step) {
  const chars = Array.from(word)
  if (step <= chars.length) return chars.slice(0, step).join('')
  return word + DOTS.slice(0, step - chars.length)
}

/** 披露完整（词 + 三个点）需要的步数。 */
export function totalSteps(word) {
  return Array.from(word).length + DOTS.length
}
