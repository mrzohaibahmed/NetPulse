/**
 * Canonicalise Cisco-style interface names so short and long forms match.
 * Gi1/0/1 == Gig1/0/1 == GigabitEthernet1/0/1
 */
const PREFIX_RULES: Array<[string, string]> = [
  ['tengigabitethernet', 'te'],
  ['tengige', 'te'],
  ['gigabitethernet', 'gi'],
  ['gige', 'gi'],
  ['gig', 'gi'],
  ['fastethernet', 'fa'],
  ['fortygigabitethernet', 'fo'],
  ['hundredgigabitethernet', 'hu'],
  ['twentyfivegigabitethernet', 'twe'],
  ['ethernet', 'et'],
  ['port-channel', 'po'],
  ['portchannel', 'po'],
  ['vlan', 'vl'],
  ['loopback', 'lo'],
  ['management', 'ma'],
]

export function canonicalizeInterfaceName(name: string | null | undefined): string {
  let text = (name || '').trim().toLowerCase().replace(/\s+/g, '')
  if (!text) return ''

  for (const [full, short] of PREFIX_RULES) {
    if (text.startsWith(full)) {
      return short + text.slice(full.length)
    }
  }
  return text
}

export function interfaceNamesMatch(
  a: string | null | undefined,
  b: string | null | undefined,
): boolean {
  const ca = canonicalizeInterfaceName(a)
  const cb = canonicalizeInterfaceName(b)
  return Boolean(ca) && ca === cb
}
