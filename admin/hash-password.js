#!/usr/bin/env node
/**
 * Sinh giá trị cho biến môi trường ADMIN_PASSWORD_HASH.
 *
 *   node admin/hash-password.js 'mật khẩu mới'
 *
 * Dán kết quả vào Dokploy → app quanly → Environment. Không commit vào repo.
 */
import crypto from 'node:crypto'

const password = process.argv[2]
if (!password) {
  console.error("Dùng: node admin/hash-password.js 'mật khẩu'")
  process.exit(1)
}

const salt = crypto.randomBytes(16)
const hash = crypto.scryptSync(password, salt, 64)
console.log(`${salt.toString('hex')}:${hash.toString('hex')}`)
