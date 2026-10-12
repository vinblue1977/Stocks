// A group is a person (the account owner or a partner, e.g. a spouse) plus a kind of holding.
export const KINDS = {
  stocks: { label: 'Stock Holdings', short: 'Stocks', chip: 'chip-stocks' },
  auto: { label: 'Automatic Investment', short: 'Automatic', chip: 'chip-auto' },
};
export const PEOPLE = ['mine', 'partner'];
export const GROUPS = {
  stocks: { person: 'mine', kind: 'stocks' },
  auto: { person: 'mine', kind: 'auto' },
  partner_stocks: { person: 'partner', kind: 'stocks' },
  partner_auto: { person: 'partner', kind: 'auto' },
};

export const MAX_TICKERS = 40;

const names = { mine: '', partner: '' };

/** Names come from the dashboard's config.json (cdk.json ownerName / partnerName). */
export function setNames(config) {
  names.mine = config.ownerName || '';
  names.partner = config.partnerName || '';
}

export const hasPartner = () => Boolean(names.partner);
export const groupFor = (person, kind) => (person === 'mine' ? kind : `${person}_${kind}`);
export const personName = (person) => names[person] || (person === 'mine' ? 'Me' : 'Partner');
export const possessive = (person) => (names[person] ? `${names[person]}'s` : person === 'mine' ? 'My' : "Partner's");
export const portfolioTitle = (person) => `${possessive(person)} Portfolio`;

/** e.g. "Stock Holdings", or "Sam · Stock Holdings" when both people are shown. */
export function groupLabel(group, { withPerson = hasPartner(), short = false } = {}) {
  const { person, kind } = GROUPS[group] || { person: 'mine', kind: group };
  const label = short ? KINDS[kind].short : KINDS[kind].label;
  return withPerson ? `${personName(person)} · ${label}` : label;
}

export const groupChip = (group) => KINDS[GROUPS[group]?.kind]?.chip || '';
