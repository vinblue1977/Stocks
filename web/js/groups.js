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
let partnerShown = false;

/** From GET /profile (deploy-time ownerName / partnerName), loaded after sign-in. */
export function setProfile(profile) {
  names.mine = profile.owner_name || '';
  names.partner = profile.partner_name || '';
  partnerShown = Boolean(profile.has_partner);
}

export const hasPartner = () => partnerShown;
export const groupFor = (person, kind) => (person === 'mine' ? kind : `${person}_${kind}`);
export const personName = (person) => names[person] || (person === 'mine' ? 'Me' : 'Partner');
export const possessive = (person) => (names[person] ? `${names[person]}'s` : person === 'mine' ? 'My' : "Partner's");
export const portfolioTitle = (person) => `${possessive(person)} Portfolio`;

/** e.g. "Stock Holdings", or "Sam · Stock Holdings" when both people are shown. */
export function groupLabel(group, { withPerson = hasPartner(), short = false } = {}) {
  const info = GROUPS[group];
  if (!info) return group || 'Unknown'; // unexpected data: show the raw value rather than failing
  const { person, kind } = info;
  const label = short ? KINDS[kind].short : KINDS[kind].label;
  return withPerson ? `${personName(person)} · ${label}` : label;
}

export const groupChip = (group) => KINDS[GROUPS[group]?.kind]?.chip || '';
