import client from './client'

export interface LoginParams {
  username: string
  password: string
}

export interface TokenData {
  access_token: string
  refresh_token: string
  user: { id: string; username: string; role: string }
}

export function login(params: LoginParams): Promise<{ data: TokenData }> {
  return client.post('/auth/login', params)
}

export function refresh(token: string): Promise<{ data: TokenData }> {
  return client.post('/auth/refresh', { refresh_token: token })
}

export function getMe(): Promise<{ data: { id: string; username: string; role: string } }> {
  return client.get('/auth/me')
}
