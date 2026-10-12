import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const dataRoot = path.resolve(scriptDir, '..', 'public', 'data')
const checkOnly = !process.argv.includes('--write')

function listJsonFiles(root) {
  const files = []
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      if (entry.name.startsWith('.')) continue
      const fullPath = path.join(directory, entry.name)
      if (entry.isDirectory()) visit(fullPath)
      else if (entry.isFile() && entry.name.toLowerCase().endsWith('.json')) files.push(fullPath)
    }
  }
  visit(root)
  return files.sort()
}

function splitSuffix(value) {
  const index = String(value).search(/[?#]/)
  if (index < 0) return { pathname: String(value), suffix: '' }
  return { pathname: String(value).slice(0, index), suffix: String(value).slice(index) }
}

function relativeResourceUrl(value, filePath) {
  const { pathname, suffix } = splitSuffix(value)
  const trailingSlash = pathname.endsWith('/')
  const targetRelative = pathname.replace(/^\/data\//, '')
  const target = path.resolve(dataRoot, ...targetRelative.split('/'))
  const fileDirectory = path.dirname(filePath)
  let relative = path.relative(fileDirectory, target).split(path.sep).join('/')
  if (!relative || !relative.startsWith('.')) relative = `./${relative}`
  if (trailingSlash && !relative.endsWith('/')) relative += '/'
  return `${relative}${suffix}`
}

function normalizeFile(filePath) {
  const source = fs.readFileSync(filePath, 'utf8')
  let replacements = 0
  const normalized = source.replace(/(["'])(\/data\/[^"']+)(\1)/g, (match, quote, value) => {
    const replacement = relativeResourceUrl(value, filePath)
    if (replacement === value) return match
    replacements += 1
    return `${quote}${replacement}${quote}`
  })
  return { normalized, replacements }
}

const files = listJsonFiles(dataRoot)
let totalReplacements = 0
let changedFiles = 0
for (const filePath of files) {
  const { normalized, replacements } = normalizeFile(filePath)
  if (!replacements) continue
  changedFiles += 1
  totalReplacements += replacements
  const relative = path.relative(dataRoot, filePath).split(path.sep).join('/')
  console.log(`${checkOnly ? 'would normalize' : 'normalized'} ${relative} (${replacements})`)
  if (!checkOnly) fs.writeFileSync(filePath, normalized, 'utf8')
}

console.log(JSON.stringify({
  mode: checkOnly ? 'check' : 'write',
  dataRoot,
  files: files.length,
  changedFiles,
  replacements: totalReplacements
}))
