import { getNominatimContact } from '../config/appConfig'

interface NominatimAddress {
  city?: string
  town?: string
  village?: string
  hamlet?: string
  municipality?: string
  county?: string
  state?: string
  country?: string
}

interface NominatimReverseResponse {
  address?: NominatimAddress
  display_name?: string
  error?: string
}

function pickPlaceName(address: NominatimAddress | undefined): string | undefined {
  if (!address) return undefined
  return (
    address.city ??
    address.town ??
    address.village ??
    address.hamlet ??
    address.municipality ??
    address.county ??
    address.state ??
    address.country
  )
}

export async function reverseGeocode(lat: number, lon: number): Promise<string | undefined> {
  const url = new URL('https://nominatim.openstreetmap.org/reverse')
  url.searchParams.set('format', 'jsonv2')
  url.searchParams.set('lat', String(lat))
  url.searchParams.set('lon', String(lon))
  url.searchParams.set('zoom', '10')
  url.searchParams.set('addressdetails', '1')

  const response = await fetch(url, {
    headers: {
      'User-Agent': getNominatimContact() ?? 'media-organizer/0.1'
    }
  })

  if (!response.ok) {
    throw new Error(`Nominatim request failed: ${response.status} ${response.statusText}`)
  }

  const data = (await response.json()) as NominatimReverseResponse
  if (data.error) return undefined
  return pickPlaceName(data.address)
}
