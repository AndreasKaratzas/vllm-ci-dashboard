// Imported first by main.ts: module imports evaluate in order, and creating the
// router normalizes the hash, so legacy links must be handled before that.
import { redirectLegacyLink } from './legacyLinks'

export const leftForClassicPage = redirectLegacyLink()
