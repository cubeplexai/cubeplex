import { redirect } from 'next/navigation'

export default function CostRedirect(): never {
  redirect('/admin/insights')
}
