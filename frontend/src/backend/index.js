import * as local from './local.js'
import * as github from './github.js'

const params = new URLSearchParams(location.search)
const isGithub =
  params.get('mode') === 'github' || location.hostname.endsWith('.github.io')

export const backend = isGithub ? github.create(params) : local.create()
