import { useEffect, useState } from 'react'
import { getToken, onTokenChange } from '@/api/client'

export function useHasToken(): boolean {
  const [has, setHas] = useState(() => !!getToken())
  useEffect(() => onTokenChange(() => setHas(!!getToken())), [])
  return has
}
