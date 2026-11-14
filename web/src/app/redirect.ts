// Imported first by main.ts: module imports evaluate in order, and creating the
// router normalizes the hash, so old links must be handled before that.
import { redirectOldLink } from './oldLinks'

export const leftForOldPage = redirectOldLink()
