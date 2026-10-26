import { Anatomy } from '../components/Anatomy'
import { AiKeyAndModel } from '../components/CopilotKeys'
import { Faq } from '../components/Faq'
import { Card } from '../components/ui'

/** Your own settings (API key, AI model), how the platform works, and FAQs. */
export function OtherPage() {
  return (
    <div className="space-y-5">
      <Card title="AI key & model" hint="(only for your account)">
        <div id="ai">
          <AiKeyAndModel />
        </div>
      </Card>
      <Card title="Anatomy" hint="(how Nivesh works, and where your data goes)">
        <div id="anatomy">
          <Anatomy />
        </div>
      </Card>
      <Card title="FAQs" hint="(how to use it, what it solves, and how; facts, not myths)">
        <div id="faq">
          <Faq />
        </div>
      </Card>
    </div>
  )
}
