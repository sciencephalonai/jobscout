import TopNav from './TopNav'
import { ResumeDropMatch } from './ResumeMatchPanel'
import { ProfilesList } from './ProfilesPanel'

/**
 * "Profile" tab — drop a resume to create/refresh a profile + see matches (top),
 * and manage all saved profiles (bottom). Merges the former Match + Profiles tabs.
 */
export default function ProfilePanel() {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <TopNav />
      <div className="mx-auto w-full max-w-4xl flex-1 overflow-y-auto px-6 py-6 space-y-8">
        <section>
          <h1 className="mb-3 text-lg font-semibold text-slate-900">Match my resume</h1>
          <ResumeDropMatch />
        </section>
        <section>
          <ProfilesList />
        </section>
      </div>
    </div>
  )
}
