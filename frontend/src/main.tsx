import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createHashRouter, Navigate, RouterProvider } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { AppShell } from '@/components/layout/AppShell'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { CampaignDetailPage } from '@/pages/CampaignDetail'
import { CampaignsPage } from '@/pages/Campaigns'
import { ClipsPage } from '@/pages/Clips'
import { CandidatesPage } from '@/pages/Candidates'
import { JobsPage } from '@/pages/Jobs'
import { OverviewPage } from '@/pages/Overview'
import { VideosPage } from '@/pages/Videos'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
    },
  },
})

// Hash router: FastAPI StaticFiles has no SPA fallback for deep links.
const router = createHashRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/overview" replace /> },
      { path: 'overview', element: <OverviewPage /> },
      { path: 'campaigns', element: <CampaignsPage /> },
      { path: 'campaigns/:id', element: <CampaignDetailPage /> },
      { path: 'jobs', element: <JobsPage /> },
      { path: 'videos', element: <VideosPage /> },
      { path: 'candidates', element: <CandidatesPage /> },
      { path: 'clips', element: <ClipsPage /> },
      { path: '*', element: <Navigate to="/overview" replace /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster richColors position="bottom-right" />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
