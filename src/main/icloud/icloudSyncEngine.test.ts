import { describe, expect, it } from 'vitest'
import { buildArgs } from './icloudSyncEngine'
import type { IcloudSyncStartRequest } from '../../shared/types/IpcContracts'

function baseRequest(overrides: Partial<IcloudSyncStartRequest> = {}): IcloudSyncStartRequest {
  return {
    appleId: 'me@example.com',
    includePhotos: true,
    includeVideos: true,
    ...overrides
  }
}

describe('buildArgs', () => {
  it('always sets up webui auth/mfa providers, the cookie/target dirs, and a flat folder structure', () => {
    const args = buildArgs(baseRequest(), 'C:\\cookies', 'C:\\target', false)

    expect(args).toEqual(
      expect.arrayContaining([
        '--username',
        'me@example.com',
        '--cookie-directory',
        'C:\\cookies',
        '--directory',
        'C:\\target',
        '--password-provider',
        'webui',
        '--mfa-provider',
        'webui',
        '--folder-structure',
        'none'
      ])
    )
  })

  it('adds --auth-only only when requested', () => {
    expect(buildArgs(baseRequest(), 'c', 't', true)).toContain('--auth-only')
    expect(buildArgs(baseRequest(), 'c', 't', false)).not.toContain('--auth-only')
  })

  it('skips photos/videos to match the includePhotos/includeVideos flags', () => {
    expect(buildArgs(baseRequest({ includePhotos: false }), 'c', 't', false)).toContain('--skip-photos')
    expect(buildArgs(baseRequest({ includeVideos: false }), 'c', 't', false)).toContain('--skip-videos')
    expect(buildArgs(baseRequest(), 'c', 't', false)).not.toContain('--skip-photos')
    expect(buildArgs(baseRequest(), 'c', 't', false)).not.toContain('--skip-videos')
  })

  it('translates the date range into --skip-created-before/--skip-created-after', () => {
    const args = buildArgs(
      baseRequest({ dateRangeStart: '2026-01-01', dateRangeEnd: '2026-06-30' }),
      'c',
      't',
      false
    )
    expect(args).toEqual(
      expect.arrayContaining(['--skip-created-before', '2026-01-01', '--skip-created-after', '2026-07-01'])
    )
  })

  it('pushes --skip-created-after one day forward so the end date is inclusive', () => {
    // icloudpd treats a bare date as that day's midnight and excludes everything after it,
    // so the raw end date would silently drop the whole last day - confirmed against a real
    // sync run's debug log where "--skip-created-after 2024-09-01" logged assets as skipped
    // "after 2024-09-01 00:00:00", which would exclude all of 2024-08-31 if passed as-is.
    const args = buildArgs(baseRequest({ dateRangeEnd: '2024-08-31' }), 'c', 't', false)
    expect(args).toEqual(expect.arrayContaining(['--skip-created-after', '2024-09-01']))
  })

  it('rolls over the month/year when the end date is the last day of one', () => {
    expect(buildArgs(baseRequest({ dateRangeEnd: '2026-12-31' }), 'c', 't', false)).toEqual(
      expect.arrayContaining(['--skip-created-after', '2027-01-01'])
    )
  })

  it('omits the date-range flags entirely when no range is set', () => {
    const args = buildArgs(baseRequest(), 'c', 't', false)
    expect(args).not.toContain('--skip-created-before')
    expect(args).not.toContain('--skip-created-after')
  })
})
