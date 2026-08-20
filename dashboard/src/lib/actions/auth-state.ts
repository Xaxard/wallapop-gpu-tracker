/**
 * Shape and initial value of the login form's action state.
 *
 * Deliberately NOT in `auth.ts`. That file is `"use server"`, and a server
 * module may only export async functions — Next validates this at runtime, not
 * at build time, so exporting a plain object from it compiles, type-checks and
 * lints cleanly and then throws on the first POST:
 *
 *   Error: A "use server" file can only export async functions, found object.
 *
 * Which is exactly what happened: the login page rendered, the gate worked, and
 * submitting the password returned a 500. The interface could have stayed in
 * `auth.ts` (types are erased before Next ever sees the module) but it lives
 * here with its value so the two cannot drift apart.
 */
export interface LoginState {
  error: string | null;
}

export const initialLoginState: LoginState = { error: null };
